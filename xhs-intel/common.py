"""Small configuration/transport boundary; no credentials in logs or artifacts."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, build_opener


def load_env(path):
    """Load only an explicitly selected, private env file; never execute it."""
    path = Path(path).expanduser()
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition('=')
        if sep and key and not key.startswith('#'):
            os.environ.setdefault(key, value.strip().strip('\"\''))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def request_json(url, payload=None, *, token='', header='X-XHS-Token', timeout=30):
    host = urlsplit(url).hostname
    private = host == 'localhost'
    try:
        private = private or ipaddress.ip_address(host).is_private
    except ValueError:
        pass
    opener = build_opener(ProxyHandler({})) if private else build_opener()
    headers = {'Content-Type': 'application/json'}
    if token:
        headers[header] = token
    req = Request(url, data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(), headers=headers)
    with opener.open(req, timeout=timeout) as response:
        data = response.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError('response_too_large')
    return json.loads(data)


def error_code(exc):
    # Upstream exceptions can embed cookies, signed URLs, or request headers.
    # Persist only their class and HTTP status, never repr/str or a traceback.
    return f'{type(exc).__name__}:{getattr(exc, "code", "")}'.rstrip(':')
