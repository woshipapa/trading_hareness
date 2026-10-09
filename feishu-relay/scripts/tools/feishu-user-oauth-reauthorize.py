#!/usr/bin/env python3
"""Re-authorize the edge relay's Feishu user OAuth after the refresh token expires.

The OAuth backfill lane (official message.list / resource reads) dies roughly
monthly when Feishu expires the refresh token; the adapter then logs
"飞书用户授权失败：The refresh token has expired" and degraded cards keep their
placeholder. Re-authorization needs a human consent click, so this helper does
everything around that one click:

1. reads FEISHU_APP_ID and QUANT_WRITE_API_KEY from config/secrets/.env.local
   in memory — values are never printed or written anywhere,
2. opens the Feishu consent page in the default browser,
3. catches the authorization code on the registered local callback
   (http://localhost:8080/callback), and
4. hands the code to the edge adapter through the feishu tunnel, so only the
   adapter (which holds FEISHU_APP_SECRET) exchanges and stores tokens.

Run on the workstation: python3 feishu-relay/scripts/tools/feishu-user-oauth-reauthorize.py
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = REPO_ROOT / "config" / "secrets" / ".env.local"
AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
# Must stay equal to the adapter's FEISHU_USER_OAUTH_REDIRECT_URI default and
# to the redirect URI registered in the Feishu app's security settings.
REDIRECT_URI = "http://localhost:8080/callback"
CALLBACK_HOST, CALLBACK_PORT = "127.0.0.1", 8080
# Mirror of REQUIRED_RELAY_SCOPES in feishu-relay/adapter/feishu-user-oauth.mjs.
SCOPES = ("auth:user.id:read im:chat:readonly im:message im:message.group_msg "
          "im:message.group_msg:get_as_user im:resource offline_access")


def read_env_keys(*names: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key in names and value:
            values[key] = value
    missing = [name for name in names if name not in values]
    if missing:
        raise SystemExit(f"missing keys in {ENV_FILE}: {', '.join(missing)}")
    return values


def wait_for_code(expected_state: str, timeout_seconds: int) -> str:
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):  # noqa: D102
            return

        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path != urllib.parse.urlsplit(REDIRECT_URI).path:
                self.send_error(404)
                return
            if query.get("state", [""])[0] != expected_state:
                self.send_error(400, "state mismatch")
                return
            code = query.get("code", [""])[0]
            if not code:
                self.send_error(400, "missing code")
                return
            result["code"] = code
            body = ("<meta charset='utf-8'><p>授权完成，可以关闭此页。"
                    "剩余步骤由终端里的脚本继续。</p>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        server = HTTPServer((CALLBACK_HOST, CALLBACK_PORT), Handler)
    except OSError as error:
        raise SystemExit(f"cannot listen on {CALLBACK_HOST}:{CALLBACK_PORT} "
                         f"(is something else using it?): {error}") from error
    server.timeout = timeout_seconds
    with server:
        server.handle_request()
    if "code" not in result:
        raise SystemExit("no authorization callback arrived (timeout or consent cancelled)")
    return result["code"]


def post_json(url: str, payload: dict[str, str], headers: dict[str, str], timeout: int = 30) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(f"adapter rejected the code (HTTP {error.code}): {detail}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--adapter", default="http://127.0.0.1:18300",
                        help="edge adapter base URL (default: the feishu tunnel)")
    parser.add_argument("--timeout", type=int, default=300,
                        help="seconds to wait for the browser consent (default 300)")
    parser.add_argument("--print-url", action="store_true",
                        help="print the consent URL instead of opening a browser "
                             "(the URL embeds the app id)")
    args = parser.parse_args()

    env = read_env_keys("FEISHU_APP_ID", "QUANT_WRITE_API_KEY")
    try:
        with urllib.request.urlopen(f"{args.adapter}/api/group-relay/status", timeout=8):
            pass
    except OSError as error:
        raise SystemExit(f"edge adapter is unreachable at {args.adapter} "
                         f"(feishu-tunnel down?): {error}") from error

    state = secrets.token_urlsafe(16)
    consent_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_id": env["FEISHU_APP_ID"], "redirect_uri": REDIRECT_URI,
        "scope": SCOPES, "state": state,
    })
    if args.print_url:
        print(consent_url)
    else:
        print("打开浏览器等待授权……（需要以这台机器上登录的飞书账号点同意）")
        webbrowser.open(consent_url)
    code = wait_for_code(state, args.timeout)
    print("已收到授权码，交给 edge adapter 换取并保存 token……")
    stored = post_json(f"{args.adapter}/internal/feishu-user-oauth",
                       {"authorization_code": code, "redirect_uri": REDIRECT_URI},
                       {"x-quant-write-key": env["QUANT_WRITE_API_KEY"]})
    print(f"status: {stored.get('status')}")
    print(f"access_expires_at: {stored.get('access_expires_at')}")
    print(f"refresh_expires_at: {stored.get('refresh_expires_at')}")
    print(f"scopes: {stored.get('scopes')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
