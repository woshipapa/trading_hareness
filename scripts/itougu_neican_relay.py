#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爱投顾内参 → 飞书 中继。

两种触发方式共用同一套"取增量 + 发飞书 + 去重"逻辑：
  1) 事件驱动：wechat-biz-relay.py 检测到爱投顾(gh_6569bb074cc9)内参推送这条"聊天消息"时，
     调用 trigger_from_push()，立刻拉正文发飞书。（近实时，零轮询）
  2) 低频兵底：launchd 每 1-2 分钟(交易时段)跑一次 `--once`，防止个别推送漏检。

去重两层：本地 state 记已发的 appendContentId + 飞书 uuid(基于 appendContentId) 幂等。
凭据：itougu 用 wechat-export-macos/itougu_auth.json 里的长效 token；飞书用 n8n/.env 的 APP_ID/SECRET。
"""
import argparse
import hashlib
import html
import hmac
import io
import json
import os
import re
import secrets
import sys
import time
import uuid
import urllib.request
import urllib.error
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import fcntl
from datetime import datetime, timezone, timedelta
from pathlib import Path

CST = timezone(timedelta(hours=8))

# ---- 配置 ----
GH = "gh_6569bb074cc9"                       # 爱投顾公众号
AUTH_FILE = Path(os.environ.get("ITOUGU_AUTH_FILE", "/Users/papa/codebase/wechat-export-macos/itougu_auth.json"))
ENV_FILE = Path(os.environ.get("ITOUGU_ENV_FILE", "/Users/papa/codebase/n8n/.env"))
STATE_FILE = Path(os.environ.get("ITOUGU_STATE_FILE", "/Users/papa/codebase/n8n/state/itougu-neican.json"))
VIDEO_QUEUE_FILE = Path(os.environ.get("ITOUGU_VIDEO_QUEUE_FILE", str(STATE_FILE.with_name("video-tasks.jsonl"))))
CHAT_IDS = [c for c in os.environ.get("ITOUGU_CHAT_IDS", "oc_570aeb3bbfb11fa2be66b25ca4568aad").split(",") if c.strip()]
# A process-local safety override for smoke tests.  When set, it wins over
# every product-specific fan-out (including Juejin and article destinations),
# so a test cannot accidentally reach a dedicated analyst group.
TEST_CHAT_IDS = [c for c in os.environ.get("ITOUGU_TEST_CHAT_IDS", "").split(",") if c.strip()]
# Optional product-specific fan-out.  It lets the dedicated 擒龙 group stay
# focused while the general public-account group continues to receive both.
QINLONG_CHAT_IDS = [c for c in os.environ.get("ITOUGU_QINLONG_CHAT_IDS", "").split(",") if c.strip()]
# Product-specific fan-out for 尾盘掘金内参. Keep the general sink as the
# default so existing deployments remain backward compatible.
JUEJIN_CHAT_IDS = [c for c in os.environ.get("ITOUGU_JUEJIN_CHAT_IDS", "").split(",") if c.strip()]
# Additional destinations for both public-account article feeds.
ARTICLE_CHAT_IDS = [c for c in os.environ.get("ITOUGU_ARTICLE_CHAT_IDS", "").split(",") if c.strip()]
API_BASE = "https://group-api.itougu.com"
PFX = "/teach-product/internalReference"
SUCCESS = 20000

# WeChat 推送里的 productId(=businessProductId) -> 名称
WATCH = {
    "1806593447818383361": "尾盘掘金内参",
    "1661993558510538753": "猎场擒龙内参",
}

# The dedicated product groups receive a provenance/disclosure line at both
# ends of the forwarded body.  The shared 公众号同步群 remains unchanged.
PRODUCT_NOTICE = "认真一手咸鱼店铺：餐厅焦糖味的momo，其他都是二手转发。"
# The raster carries one standalone shop watermark.  It is deliberately not
# the selectable-body notice above, so the image has no repeated prefix/suffix.
IMAGE_SHOP_WATERMARK = "咸鱼店铺：餐厅焦糖味的momo"
MAX_FEISHU_TEXT_CHARS = 28000
MAX_PRODUCT_IMAGE_CHARS = 5000
EXTERNAL_LINK_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
WATERMARK_SECRET_FILE = Path(os.environ.get(
    "ITOUGU_WATERMARK_SECRET_FILE", str(STATE_FILE.with_name(STATE_FILE.name + ".watermark-secret"))))
_DEFAULT_WATERMARK_FONTS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
)
_DEFAULT_EMOJI_FONTS = (
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
)
WATERMARK_FONT_FILE = Path(os.environ.get(
    "ITOUGU_WATERMARK_FONT_FILE",
    next((path for path in _DEFAULT_WATERMARK_FONTS if Path(path).is_file()), _DEFAULT_WATERMARK_FONTS[0])))
EMOJI_FONT_FILE = Path(os.environ.get(
    "ITOUGU_EMOJI_FONT_FILE",
    next((path for path in _DEFAULT_EMOJI_FONTS if Path(path).is_file()), _DEFAULT_EMOJI_FONTS[0])))
_watermark_secret_lock = threading.Lock()
_watermark_secret_value = None
_watermark_image_lock = threading.Lock()
_watermark_image_keys = {}
_product_font_lock = threading.Lock()
_product_fonts = None
_product_fonts_path = None
_emoji_font_lock = threading.Lock()
_emoji_font = None
_emoji_font_path = None
_emoji_font_size = None


def chat_ids_for_product(business_id, override=None):
    """Return the configured destinations for one internal-reference product."""
    if override is not None:
        return list(dict.fromkeys(override))
    if TEST_CHAT_IDS:
        return list(dict.fromkeys(TEST_CHAT_IDS))
    destinations = list(CHAT_IDS)
    if business_id == "1661993558510538753" and QINLONG_CHAT_IDS:
        destinations.extend(QINLONG_CHAT_IDS)
    if business_id == "1806593447818383361" and JUEJIN_CHAT_IDS:
        destinations.extend(JUEJIN_CHAT_IDS)
    return list(dict.fromkeys(destinations))


def dedicated_chat_ids_for_product(business_id, override=None):
    """Return only product-specific destinations, excluding the shared sink.

    Explicit test destinations must never receive production fan-out or the
    product notice.  A product-specific environment value may include the
    shared destination for backwards compatibility; remove it here so the
    shared group gets the unmodified body exactly once.
    """
    if override is not None or TEST_CHAT_IDS:
        return []
    if business_id == "1661993558510538753":
        configured = QINLONG_CHAT_IDS
    elif business_id == "1806593447818383361":
        configured = JUEJIN_CHAT_IDS
    else:
        configured = []
    shared = set(CHAT_IDS)
    return list(dict.fromkeys(chat_id for chat_id in configured if chat_id not in shared))


def wrap_product_message(text, watermark_id=None):
    """Add the requested notice and, when supplied, a visible dynamic marker."""
    body = str(text or "")
    if watermark_id:
        marker = "【动态水印 %s】" % watermark_id
        return "%s\n\n%s\n\n%s\n\n%s\n\n%s" % (
            marker, PRODUCT_NOTICE, body, PRODUCT_NOTICE, marker)
    return "%s\n\n%s\n\n%s" % (PRODUCT_NOTICE, body, PRODUCT_NOTICE)


def is_dedicated_destination(chat_id):
    """Whether a chat is one of the two product-specific Feishu exits."""
    normalized = str(chat_id or "").strip()
    return normalized in {
        dedicated_id
        for business_id in WATCH
        for dedicated_id in dedicated_chat_ids_for_product(business_id)
    }


def build_product_card(title, text=None, image_key=None, image_alt=""):
    """Build the card JSON 2.0 shape used by the adapter's relay path."""
    elements = []
    if image_key:
        elements.append({
            "tag": "img",
            "img_key": str(image_key),
            "alt": {"tag": "plain_text", "content": str(image_alt or "")[:200]},
        })
    if text is not None:
        elements.append({"tag": "div", "text": {"tag": "plain_text", "content": str(text or "")}})
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "enable_forward_interaction": False},
        "header": {"title": {"tag": "plain_text", "content": str(title or "")[:120]}},
        "body": {
            "direction": "vertical",
            "elements": elements,
        },
    }


def build_link_post(title, text):
    """Build a native post so Feishu renders external URLs as clickable links."""
    body = str(text or "")
    elements = []
    cursor = 0
    for match in EXTERNAL_LINK_RE.finditer(body):
        if match.start() > cursor:
            elements.append({"tag": "text", "text": body[cursor:match.start()]})
        raw_url = match.group(0)
        url = raw_url.rstrip(".,;:!?)]}，。；：！？）》】")
        if url:
            href = url if re.match(r"^https?://", url, re.IGNORECASE) else "https://" + url
            elements.append({"tag": "a", "text": url, "href": href})
            cursor = match.start() + len(url)
        else:
            elements.append({"tag": "text", "text": raw_url})
            cursor = match.end()
        if cursor < match.end():
            elements.append({"tag": "text", "text": body[cursor:match.end()]})
            cursor = match.end()
    if cursor < len(body):
        elements.append({"tag": "text", "text": body[cursor:]})
    if not elements:
        elements = [{"tag": "text", "text": body}]
    return {"zh_cn": {"title": str(title or "")[:120], "content": [elements]}}


def _watermark_secret():
    """Load or create a per-install HMAC key without exposing it in logs."""
    configured = os.environ.get("ITOUGU_WATERMARK_SECRET", "").strip()
    if configured:
        return configured.encode("utf-8")
    global _watermark_secret_value
    with _watermark_secret_lock:
        if _watermark_secret_value:
            return _watermark_secret_value
        try:
            value = WATERMARK_SECRET_FILE.read_bytes().strip()
        except (FileNotFoundError, OSError):
            value = b""
        if not value:
            value = secrets.token_bytes(32)
            WATERMARK_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(str(WATERMARK_SECRET_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                value = WATERMARK_SECRET_FILE.read_bytes().strip()
            else:
                try:
                    os.write(fd, value)
                    os.fsync(fd)
                finally:
                    os.close(fd)
        _watermark_secret_value = value
        return value


def watermark_id(chat_id, text, dedup_seed=""):
    """Return a stable, destination-specific short marker for one delivery."""
    content_hash = hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()
    payload = "v1|%s|%s|%s" % (str(chat_id or "").strip(), str(dedup_seed or ""), content_hash)
    digest = hmac.new(_watermark_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return "W1-%s" % digest


def contains_external_link(text):
    """Link-bearing midday/evening notices stay selectable text for usability."""
    return bool(EXTERNAL_LINK_RE.search(str(text or "")))


_DYNAMIC_WATERMARK_LINE_RE = re.compile(r"^【动态水印 W1-[0-9a-f]{16}】$")


def image_body_text(text):
    """Remove relay-only outer wrappers before putting a body into the PNG.

    The dedicated text path keeps both the notice and the dynamic marker as
    selectable text.  The image already carries the shop attribution in its
    header and a tiled dynamic fingerprint, so repeating those wrapper lines
    inside the raster wastes vertical space and makes the body harder to read.
    Only exact outer lines are removed; identical wording in the actual body
    is preserved.
    """
    parts = str(text or "").split("\n\n")
    while parts and (parts[0] == PRODUCT_NOTICE or _DYNAMIC_WATERMARK_LINE_RE.fullmatch(parts[0])):
        parts.pop(0)
    while parts and (parts[-1] == PRODUCT_NOTICE or _DYNAMIC_WATERMARK_LINE_RE.fullmatch(parts[-1])):
        parts.pop()
    return "\n\n".join(parts)


def _is_emoji_codepoint(codepoint):
    """Recognise the emoji/symbol ranges used by Itougu message templates."""
    return (
        0x1F000 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x27BF
        or 0x2300 <= codepoint <= 0x23FF
    )


def _is_emoji_modifier(codepoint):
    return (
        0xFE00 <= codepoint <= 0xFE0F
        or 0x1F3FB <= codepoint <= 0x1F3FF
        or codepoint == 0x20E3
    )


def _split_emoji_runs(text):
    """Split text into ordinary and emoji runs without breaking ZWJ glyphs."""
    runs = []
    current = []
    current_is_emoji = None
    index = 0
    value = str(text or "")

    def flush():
        nonlocal current
        if current:
            runs.append(("".join(current), bool(current_is_emoji)))
            current = []

    while index < len(value):
        char = value[index]
        codepoint = ord(char)
        is_emoji = _is_emoji_codepoint(codepoint)
        if is_emoji:
            if current_is_emoji is not True:
                flush()
                current_is_emoji = True
            current.append(char)
            index += 1
            # Keep variation selectors, skin-tone modifiers, keycap marks and
            # a following ZWJ emoji in the same raster run.
            while index < len(value):
                next_codepoint = ord(value[index])
                if _is_emoji_modifier(next_codepoint):
                    current.append(value[index])
                    index += 1
                    continue
                if next_codepoint == 0x200D and index + 1 < len(value):
                    joined = ord(value[index + 1])
                    if _is_emoji_codepoint(joined):
                        current.extend((value[index], value[index + 1]))
                        index += 2
                        continue
                break
            continue
        if current_is_emoji is not False:
            flush()
            current_is_emoji = False
        current.append(char)
        index += 1
    flush()
    return runs


def _load_emoji_font(ImageFont):
    """Load the native bitmap strike for the configured colour emoji font."""
    global _emoji_font, _emoji_font_path, _emoji_font_size
    font_path = str(EMOJI_FONT_FILE)
    with _emoji_font_lock:
        if _emoji_font_path == font_path:
            return _emoji_font, _emoji_font_size
        _emoji_font = None
        _emoji_font_size = None
        _emoji_font_path = font_path
        if not EMOJI_FONT_FILE.is_file():
            return None, None
        # Noto Color Emoji exposes a single 109px bitmap strike; Apple Color
        # Emoji accepts arbitrary sizes, so try the native strike first and
        # then the normal body/label sizes as a portable fallback.
        for size in (109, 34, 23, 58):
            try:
                _emoji_font = ImageFont.truetype(font_path, size, index=0)
            except OSError:
                continue
            _emoji_font_size = size
            break
        return _emoji_font, _emoji_font_size


def _draw_text_with_emoji(image, draw, xy, text, primary_font, emoji_font_info, fill):
    """Draw text with colour emoji tiles scaled to the surrounding font size."""
    from PIL import Image, ImageDraw

    x, y = float(xy[0]), float(xy[1])
    emoji_font, emoji_native_size = emoji_font_info
    target_size = max(1, int(getattr(primary_font, "size", 34)))
    for run, is_emoji in _split_emoji_runs(text):
        if not is_emoji or emoji_font is None or not emoji_native_size:
            draw.text((round(x), round(y)), run, font=primary_font, fill=fill)
            x += float(draw.textlength(run, font=primary_font))
            continue
        bbox = emoji_font.getbbox(run)
        source_width = max(1, int(bbox[2] - bbox[0]))
        source_height = max(1, int(bbox[3] - bbox[1]))
        pad = 8
        tile = Image.new("RGBA", (source_width + pad * 2, source_height + pad * 2), (0, 0, 0, 0))
        tile_draw = ImageDraw.Draw(tile)
        tile_draw.text((pad - bbox[0], pad - bbox[1]), run, font=emoji_font,
                       embedded_color=True)
        scale = target_size / float(emoji_native_size)
        if scale != 1.0:
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            tile = tile.resize((max(1, round(tile.width * scale)),
                                max(1, round(tile.height * scale))), resampling)
        image.paste(tile, (round(x), round(y)), tile)
        x += source_width * scale


def _wrap_image_text(text, font, max_width):
    """Wrap CJK/Latin text with a bounded-cost, conservative character width."""
    # CJK glyphs are approximately one font-size wide.  Avoid measuring every
    # candidate substring with FreeType: this function runs on every message.
    max_chars = max(1, max_width // max(1, int(getattr(font, "size", 34))))
    lines = []
    for paragraph in str(text or "").splitlines() or [""]:
        if not paragraph:
            lines.append("")
            continue
        lines.extend(paragraph[index:index + max_chars] for index in range(0, len(paragraph), max_chars))
    return lines or [""]


def render_product_image(title, text, watermark_id_value):
    """Render the complete dedicated message into one PNG with a tiled fingerprint."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("图片正文渲染依赖 Pillow 未安装") from exc
    if not WATERMARK_FONT_FILE.is_file():
        raise RuntimeError("图片正文渲染缺少中文字库: %s" % WATERMARK_FONT_FILE)
    global _product_fonts, _product_fonts_path
    with _product_font_lock:
        font_path = str(WATERMARK_FONT_FILE)
        if _product_fonts is None or _product_fonts_path != font_path:
            _product_fonts = (
                ImageFont.truetype(font_path, 34, index=0),
                ImageFont.truetype(font_path, 23, index=0),
                ImageFont.truetype(font_path, 58, index=0),
            )
            _product_fonts_path = font_path
        body_font, label_font, logo_font = _product_fonts
    emoji_font_info = _load_emoji_font(ImageFont)
    width = 1600
    padding = 72
    line_height = 52
    lines = _wrap_image_text(text, body_font, width - padding * 2)
    height = min(11800, max(360, 210 + len(lines) * line_height))
    if 210 + len(lines) * line_height > height:
        raise RuntimeError("图片正文过长，请缩短单条消息")

    image = Image.new("RGB", (width, height), "#fbfbfb")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 112), fill="#c9362c")
    _draw_text_with_emoji(image, draw, (padding, 24), "momo", logo_font, emoji_font_info, "#ffffff")
    _draw_text_with_emoji(
        image, draw, (330, 39), "%s · 专属图片正文" % str(title or "")[:70],
        label_font, emoji_font_info, "#ffffff")
    _draw_text_with_emoji(
        image, draw, (padding, 140), IMAGE_SHOP_WATERMARK,
        label_font, emoji_font_info, "#777777")
    y = 185
    for line in lines:
        _draw_text_with_emoji(image, draw, (padding, y), line, body_font, emoji_font_info, "#202124")
        y += line_height

    # Low-opacity diagonal marks are repeated over the complete raster so
    # cropping a single corner does not remove the provenance evidence.
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    mark = "momo · %s" % watermark_id_value
    for row in range(-height, height + width, 118):
        for col in range(-width, width * 2, 520):
            _draw_text_with_emoji(
                overlay, overlay_draw, (col + row // 3, row), mark,
                label_font, emoji_font_info, (170, 35, 35, 34))
    rendered = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    output = io.BytesIO()
    rendered.save(output, format="PNG", compress_level=3)
    return output.getvalue()


def _upload_product_image(token, image):
    """Upload one rendered message image and return its Feishu image key."""
    cache_key = hashlib.sha256(image).hexdigest()
    with _watermark_image_lock:
        cached = _watermark_image_keys.get(cache_key)
    if cached:
        return cached
    boundary = ("----itougu-watermark-%s" % uuid.uuid4().hex).encode("ascii")
    body = b"".join((
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="image_type"\r\n\r\n'
        b"message\r\n",
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="image"; filename="itougu-product-watermark.png"\r\n'
        b"Content-Type: image/png\r\n\r\n" + image + b"\r\n",
        b"--" + boundary + b"--\r\n",
    ))
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/images",
        data=body,
        method="POST",
        headers={
            "content-type": "multipart/form-data; boundary=" + boundary.decode("ascii"),
            "authorization": "Bearer " + token,
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    if result.get("code") not in (None, 0):
        raise RuntimeError("飞书图片正文上传失败: code=%s msg=%s" % (result.get("code"), result.get("msg")))
    image_key = result.get("image_key") or result.get("data", {}).get("image_key")
    if not image_key:
        raise RuntimeError("飞书图片正文上传未返回 image_key")
    with _watermark_image_lock:
        if len(_watermark_image_keys) >= 32:
            _watermark_image_keys.pop(next(iter(_watermark_image_keys)))
        _watermark_image_keys[cache_key] = str(image_key)
    return str(image_key)


def message_chunks_for_destination(chat_id, text, dedup_seed=""):
    """Split text while keeping a complete dynamic marker on every chunk."""
    body = str(text or "")
    normalized = str(chat_id or "").strip()
    dedicated = is_dedicated_destination(normalized)
    if not dedicated:
        return [body[i:i + MAX_FEISHU_TEXT_CHARS] for i in range(0, len(body), MAX_FEISHU_TEXT_CHARS)] or [""]
    # MAX_PRODUCT_IMAGE_CHARS no longer applies: dedicated destinations always
    # send plain post/text now (send_feishu dropped the image-card path), so
    # every chunk can use the full text budget instead of the tighter size
    # that used to keep a rendered image legible.
    marker = watermark_id(normalized, body, dedup_seed)
    overhead = len(wrap_product_message("", marker))
    available = max(1, MAX_FEISHU_TEXT_CHARS - overhead)
    return [wrap_product_message(body[i:i + available], marker)
            for i in range(0, len(body), available)] or [wrap_product_message("", marker)]


def message_for_destination(chat_id, text, dedup_seed=""):
    """Apply the product notice only at the two named product-group exits."""
    normalized = str(chat_id or "").strip()
    return (wrap_product_message(text, watermark_id(normalized, text, dedup_seed))
            if is_dedicated_destination(normalized) else text)


def article_chat_ids():
    """Return general plus explicitly registered public-article destinations."""
    if TEST_CHAT_IDS:
        return list(dict.fromkeys(TEST_CHAT_IDS))
    return list(dict.fromkeys([*CHAT_IDS, *ARTICLE_CHAT_IDS]))

_feishu_token = {"value": "", "expires_at": 0.0}
_tag_re = re.compile(r"<[^>]+>")
_fetch_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="itougu-fetch")
_delivery_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="itougu-send")
_feishu_token_lock = threading.Lock()
_deliver_lock = threading.RLock()


# ---------------- itougu ----------------
def load_headers():
    if not AUTH_FILE.exists():
        raise RuntimeError("缺少 %s（先用 itougu_capture.py 抓一次凭据）" % AUTH_FILE)
    h = dict(json.loads(AUTH_FILE.read_text(encoding="utf-8")).get("headers", {}))
    if not h.get("Authorization"):
        raise RuntimeError("itougu_auth.json 里没有 Authorization")
    h["Content-Type"] = "application/json"
    return h


def itougu_call(path, body, headers):
    data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(API_BASE + path, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=25) as r:
        j = json.loads(r.read().decode("utf-8", "ignore"))
    code = j.get("code")
    msg = str(j.get("msg", ""))
    if code != SUCCESS and any(k in msg for k in ("登录", "token", "Token", "失效", "过期", "鉴权")):
        raise RuntimeError("itougu token 失效：%s（请重跑 itougu_capture.py）" % (msg or code))
    return j


def fetch_meta(business_id, headers):
    try:
        d = (itougu_call(PFX + "/user/getInfoById", {"businessProductId": business_id}, headers).get("data") or {})
    except Exception:
        return {}
    cons = d.get("consultants") or []
    return {"productName": d.get("productName"), "consultants": "/".join(c.get("consultantName", "") for c in cons)}


def fetch_append(business_id, headers, page_size=30):
    j = itougu_call(PFX + "/appendContent/list", {"businessProductId": business_id, "pageNo": 1, "pageSize": page_size}, headers)
    return (j.get("data") or {}).get("listResult") or []


def parallel_fetch(items, fetcher, max_workers=4):
    """Fetch independent upstream resources concurrently, preserving item order.

    Only the network-bound fetch is parallelized.  Callers process and send the
    returned records serially so source ordering and durable dedupe state remain
    deterministic.  The shared bounded executor avoids creating a thread pool
    on every 15-second poll.
    """
    values = list(items)
    if len(values) <= 1:
        return [(values[0], fetcher(values[0]))] if values else []
    workers = max(1, min(int(max_workers or 1), len(values), 4))
    # Submit remaining values while retaining a hard upper bound on in-flight
    # requests.  The simple batches make the limit explicit and avoid an
    # unbounded queue when a future caller supplies many products/circles.
    results = []
    for offset in range(0, len(values), workers):
        batch = values[offset:offset + workers]
        futures = [(value, _fetch_executor.submit(fetcher, value)) for value in batch]
        results.extend((value, future.result()) for value, future in futures)
    return results


def html2text(s):
    if not s:
        return ""
    s = s.replace("</p>", "\n").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    s = html.unescape(_tag_re.sub("", s))
    return "\n".join(ln.strip() for ln in s.splitlines() if ln.strip())


def item_consultant(it):
    c = it.get("consultantName")
    if isinstance(c, str) and c.strip().startswith("["):
        try:
            return "/".join(x.get("consultantName", "") for x in json.loads(c))
        except Exception:
            pass
    return c or ""


DEAL = {0: "🟢买入/加仓", 1: "🔴卖出/离场"}


def _trade_detail(it):
    d = it.get("stockTransactionDetail") or it.get("simulateOperationJson")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:
            return None
    return d if isinstance(d, dict) and d.get("stockName") else None


def trade_line(it):
    d = _trade_detail(it)
    if not d:
        return ""
    code, mkt = d.get("stockCode", ""), (d.get("mkt") or "").upper()
    who = "%s（%s.%s）" % (d.get("stockName", ""), code, mkt) if code else d.get("stockName", "")
    parts = ["📊 %s %s" % (DEAL.get(d.get("dealType"), "操作"), who)]
    if d.get("price") is not None:
        parts.append("价位 %s" % d["price"])
    if d.get("position") is not None:
        parts.append("仓位 %s" % d["position"])
    return "　".join(parts)


def report_line(it):
    rl = it.get("reportList") or []
    if not rl or not isinstance(rl, list):
        return ""
    r = rl[0]
    bits = [x for x in ["📄 研报", r.get("orgName", ""), r.get("emRatingName", ""),
                        "《%s》" % r.get("title", "") if r.get("title") else "", r.get("filePath", "")] if x]
    return " ".join(bits)


def video_line(it):
    """Return a text-only link for an attached Itougu replay video.

    The API has used both an object and a JSON-encoded string for ``videoInfo``
    across product versions.  We intentionally forward only the public URL
    and title; the relay never downloads or proxies the media bytes.
    """
    info = it.get("videoInfo") or it.get("video_info")
    if isinstance(info, str):
        try:
            info = json.loads(info)
        except (TypeError, json.JSONDecodeError):
            info = None
    if not isinstance(info, dict):
        return ""
    url = str(info.get("videoUrl") or info.get("videoURL") or info.get("url") or "").strip()
    if not url or not re.match(r"^https?://", url, re.IGNORECASE):
        return ""
    title = str(info.get("videoName") or info.get("name") or "复盘视频").strip()
    return "〔复盘视频〕%s：%s" % (title, url)


def video_task(it, product_name):
    """Build a small, secret-free task for the owner media worker."""
    info = it.get("videoInfo") or it.get("video_info")
    if isinstance(info, str):
        try:
            info = json.loads(info)
        except (TypeError, json.JSONDecodeError):
            info = None
    if not isinstance(info, dict):
        return None
    url = str(info.get("videoUrl") or info.get("videoURL") or info.get("url") or "").strip()
    if not url or not re.match(r"^https://voss\.itougu\.com/", url, re.IGNORECASE):
        return None
    return {
        "task_key": "itougu-video:%s" % str(it.get("appendContentId") or url),
        "append_content_id": str(it.get("appendContentId") or ""),
        "product": product_name,
        "published_at": it.get("publishTime") or it.get("createTime") or "",
        "video_name": str(info.get("videoName") or info.get("name") or "复盘视频"),
        "video_id": str(it.get("videoId") or info.get("videoId") or ""),
        "url": url,
    }


def enqueue_video_task(it, product_name, state):
    """Persist one idempotent task without fetching media bytes."""
    task = video_task(it, product_name)
    if not task:
        return False
    queued = state.setdefault("video_enqueued", {})
    if task["task_key"] in queued:
        return False
    VIDEO_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with VIDEO_QUEUE_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(task, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    queued[task["task_key"]] = {"queued_at": datetime.now(timezone.utc).isoformat(), "url": task["url"]}
    return True


def format_item(name, it, delivery_label=""):
    when = it.get("publishTime") or it.get("createTime") or ""
    who = item_consultant(it)
    title = "%s · %s" % (name, who) if who else name
    if delivery_label:
        # Keep the delivery-path marker terse.  It is an operational label,
        # not a disclosure of credentials, endpoints, or listener details.
        title = "[%s] %s" % (delivery_label, title)
    lines = ["🕐 %s" % when]
    trade = trade_line(it)
    if trade:
        lines.append(trade)
    body = html2text(it.get("content"))
    if body:
        lines.append(body)
    video = video_line(it)
    if video:
        lines.append(video)
    report = report_line(it)
    if report:
        lines.append(report)
    tip = html2text(it.get("tipContent"))
    if tip:
        lines.append("〔提示〕" + tip.replace("\n", " "))
    if it.get("stockOfPool"):
        lines.append("〔股票池〕" + str(it["stockOfPool"]))
    return title, "\n".join(lines)


# ---------------- 飞书 ----------------
def load_feishu_env():
    if os.environ.get("FEISHU_APP_ID") and os.environ.get("FEISHU_APP_SECRET"):
        return
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k in {"FEISHU_APP_ID", "FEISHU_APP_SECRET"} and not os.environ.get(k):
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                os.environ[k] = v
    except OSError:
        pass


def feishu_token():
    with _feishu_token_lock:
        now = time.time()
        if _feishu_token["value"] and _feishu_token["expires_at"] > now + 60:
            return _feishu_token["value"]
        load_feishu_env()
        app_id, secret = os.environ.get("FEISHU_APP_ID", ""), os.environ.get("FEISHU_APP_SECRET", "")
        if not app_id or not secret:
            raise RuntimeError("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET")
        body = json.dumps({"app_id": app_id, "app_secret": secret}).encode()
        req = urllib.request.Request("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                                     data=body, method="POST", headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.loads(r.read().decode("utf-8"))
        if res.get("code") not in (None, 0) or not res.get("tenant_access_token"):
            raise RuntimeError("飞书 token 失败: %s" % res.get("msg"))
        _feishu_token.update(value=res["tenant_access_token"], expires_at=now + max(60, int(res.get("expire", 7200))))
        return _feishu_token["value"]


_feishu_webhook_map_lock = threading.Lock()
_feishu_webhook_map_value = None
_feishu_webhook_map_raw = None


def _feishu_webhook_map():
    """Parse ITOUGU_FEISHU_WEBHOOKS ("chat_id=url;chat_id2=url2") once per value.

    Re-parses only when the env var text itself changes, so a config reload
    (env re-read at process start) never needs a code change.
    """
    raw = os.environ.get("ITOUGU_FEISHU_WEBHOOKS", "")
    global _feishu_webhook_map_value, _feishu_webhook_map_raw
    with _feishu_webhook_map_lock:
        if raw == _feishu_webhook_map_raw and _feishu_webhook_map_value is not None:
            return _feishu_webhook_map_value
        mapping = {}
        for pair in raw.split(";"):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            chat_id, url = pair.split("=", 1)
            chat_id, url = chat_id.strip(), url.strip()
            if chat_id and url:
                mapping[chat_id] = url
        _feishu_webhook_map_value, _feishu_webhook_map_raw = mapping, raw
        return mapping


def _feishu_webhook_url(chat_id):
    return _feishu_webhook_map().get(str(chat_id or "").strip())


def _post_via_feishu_webhook(url, msg_type, content):
    """Send through a custom-bot webhook instead of the tenant API.

    Webhook calls do not count against the tenant's monthly API quota, but
    the payload shape differs from im/v1/messages: an interactive card is a
    top-level "card" object (not a JSON-string "content"), and there is no
    "uuid" field, so a retried send after a timed-out response can duplicate
    (acceptable for a periodic report/notice; im/v1/messages is still used
    everywhere idempotency matters more than quota).
    """
    if msg_type == "interactive":
        body = {"msg_type": "interactive", "card": content}
    elif msg_type == "post":
        body = {"msg_type": "post", "content": {"post": content}}
    else:
        raise RuntimeError("webhook 发送暂不支持的消息类型：%s" % msg_type)
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(),
                                  method="POST", headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        res = json.loads(r.read().decode("utf-8"))
    if res.get("code") not in (None, 0):
        raise RuntimeError("飞书 webhook 发送失败: code=%s msg=%s" % (res.get("code"), res.get("msg")))


def send_feishu(chat_id, title, text, dedup_seed):
    # 2026-09: dedicated destinations (擒龙内参/尾盘掘金) no longer render a
    # watermarked image card — always send plain post/text now. render_product_image /
    # build_product_card / watermark_id stay defined (the edge deploy contract
    # check in deploy-itougu-neican.sh still exercises them directly) but are
    # no longer called from here.
    token = feishu_token()
    chunks = message_chunks_for_destination(chat_id, text, dedup_seed)
    for idx, chunk in enumerate(chunks):
        t = title if idx == 0 else "%s（续 %d/%d）" % (title, idx + 1, len(chunks))
        msg_type = "post"
        content = build_link_post(t, chunk)
        webhook_url = _feishu_webhook_url(chat_id)
        if webhook_url:
            _post_via_feishu_webhook(webhook_url, msg_type, content)
            continue
        body = json.dumps({
            "receive_id": chat_id, "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
            "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, "itougu-neican:%s:%s:%d" % (dedup_seed, chat_id, idx))),
        }, ensure_ascii=False).encode()
        req = urllib.request.Request("https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                                     data=body, method="POST",
                                     headers={"content-type": "application/json", "authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=20) as r:
            res = json.loads(r.read().decode("utf-8"))
        if res.get("code") not in (None, 0):
            raise RuntimeError("飞书发送失败: code=%s msg=%s" % (res.get("code"), res.get("msg")))


def send_feishu_many(chat_ids, title, text, dedup_seed):
    """Fan out one immutable message to a bounded set of chats concurrently."""
    destinations = list(dict.fromkeys(str(chat_id).strip() for chat_id in chat_ids if str(chat_id).strip()))
    if not destinations:
        return
    futures = [(chat_id, _delivery_executor.submit(send_feishu, chat_id, title, text, dedup_seed)) for chat_id in destinations[:4]]
    failures = []
    for chat_id, future in futures:
        try:
            future.result()
        except Exception as exc:
            failures.append("%s: %s" % (chat_id, exc))
    if failures:
        raise RuntimeError("；".join(failures))


# ---------------- state ----------------
def load_state():
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(s.get("seen"), dict):
            return s
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"seen": {}}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    for bid, ids in state["seen"].items():
        state["seen"][bid] = ids[-500:]     # 每个内参最多记 500 个已发 id
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


@contextmanager
def state_file_lock():
    """Serialize local and fallback pollers sharing the JSON dedupe state."""
    lock_path = STATE_FILE.with_name(STATE_FILE.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


# ---------------- 核心：取增量并发送 ----------------
def _deliver_new_unlocked(products=None, chat_ids=None, dry_run=False, bootstrap=False, verbose=True, delivery_label=""):
    products = products or WATCH
    chat_ids = chat_ids or None
    headers = load_headers()
    state = load_state()
    total_sent = 0
    fetched = {}
    fetch_errors = {}
    # Product endpoints are independent. Fetch them concurrently, then handle
    # each product in declaration order so messages within a product remain
    # oldest-to-newest and state writes stay deterministic.
    def fetch_one(bid):
        try:
            return {"items": fetch_append(bid, headers)}
        except Exception as exc:
            return {"error": exc}
    for bid, result in parallel_fetch(list(products), fetch_one, max_workers=4):
        if result.get("error") is not None:
            fetch_errors[bid] = result["error"]
        else:
            fetched[bid] = result.get("items", [])
    for bid, name in products.items():
        if bid in fetch_errors:
            if verbose:
                print("[%s] 拉取失败: %s" % (name, fetch_errors[bid]), flush=True)
            continue
        items = fetched.get(bid, [])
        seen = set(state["seen"].get(bid, []))
        new = [it for it in items if str(it.get("appendContentId")) not in seen]
        new.reverse()   # 旧→新 顺序发
        if bootstrap:
            state["seen"][bid] = state["seen"].get(bid, []) + [str(it.get("appendContentId")) for it in items]
            if verbose:
                print("[%s] bootstrap: 标记 %d 条为已读，不发送" % (name, len(items)), flush=True)
            continue
        for it in new:
            aid = str(it.get("appendContentId"))
            enqueue_video_task(it, name, state)
            title, text = format_item(name, it, delivery_label=delivery_label)
            destinations = chat_ids_for_product(bid, override=chat_ids)
            dedicated_destinations = dedicated_chat_ids_for_product(bid, override=chat_ids)
            if dry_run:
                if verbose:
                    print("── DRY [%s] %s\n%s\n" % (name, title, text[:400]), flush=True)
                    if dedicated_destinations:
                        print("── DRY [%s 专属出口] %s\n%s\n" % (
                            name, title, wrap_product_message(text)[:400]), flush=True)
            else:
                # The final sender applies the notice by destination, keeping
                # the shared 公众号同步群 unchanged while covering every
                # message type that uses a named product-group exit.
                send_feishu_many(destinations, title, text, aid)
                total_sent += 1
                if verbose:
                    print("✅ 已发飞书 [%s] %s (%s)" % (name, title, aid), flush=True)
            state["seen"].setdefault(bid, []).append(aid)
    if not dry_run:
        save_state(state)
    return total_sent


def deliver_new(products=None, chat_ids=None, dry_run=False, bootstrap=False, verbose=True, delivery_label=""):
    """Fetch and deliver incrementally under a process/file-wide state lock."""
    with _deliver_lock, state_file_lock():
        return _deliver_new_unlocked(products=products, chat_ids=chat_ids, dry_run=dry_run,
                                     bootstrap=bootstrap, verbose=verbose, delivery_label=delivery_label)


def trigger_from_push(username, article_url, verbose=False):
    """供 wechat-biz-relay 调用：看到爱投顾内参这条聊天消息就触发。非致命。"""
    try:
        if username != GH or "internalReference" not in (article_url or ""):
            return 0
        m = re.search(r"productId=(\d+)", article_url or "")
        pid = m.group(1) if m else None
        if not pid or pid not in WATCH:
            return 0            # 只处理目标内参的推送，其它内参卡片忽略
        return deliver_new(products={pid: WATCH[pid]}, verbose=verbose, delivery_label="database")
    except Exception as e:
        if verbose:
            print("itougu trigger 失败(忽略): %s" % e, flush=True)
        return 0


def ensure_baseline(products):
    """首次运行(state 文件不存在)时静默建立去重基线：标记当前所有内参为已读、不发历史。"""
    if STATE_FILE.exists():
        return
    try:
        deliver_new(products=products, bootstrap=True, verbose=True)
        print("首次运行：已建立去重基线（历史不发送）", flush=True)
    except Exception as e:
        print("建立基线失败(忽略，下轮再试): %s" % e, flush=True)


def poll_public_views(verbose=False, delivery_label="poll"):
    """Poll registered Itougu public-circle view APIs without WeChat tables.

    The import is lazy because the public-article module reuses this module's
    auth and Feishu helpers.  Keeping it here makes the systemd poller the
    single API-driven path after the local SQLite/WAL watcher is disabled.
    """
    try:
        import itougu_public_article_relay as public_relay
        return int(public_relay.poll_views(verbose=verbose, delivery_label=delivery_label) or 0)
    except Exception as exc:
        if verbose:
            print("研习社公开观点轮询失败(忽略): %s" % exc, flush=True)
        return 0


MORNING = (9 * 60 + 30, 11 * 60 + 30)   # A股上午
AFTERNOON = (13 * 60, 15 * 60)          # A股下午


def _minutes(now):
    return now.hour * 60 + now.minute


def in_trading_hours(now=None):
    now = now or datetime.now(CST)
    if now.weekday() >= 5:          # 周末
        return False
    hm = _minutes(now)
    return MORNING[0] <= hm <= MORNING[1] or AFTERNOON[0] <= hm <= AFTERNOON[1]


def in_midday_break(now=None):
    """A股午间休市 11:30-13:00。

    交易所不开，但内参与公开圈子照发（复盘、次日计划、观点），所以这一段必须
    继续拉取，否则午休期间发布的内容要等到 13:00 才会被看见。
    """
    now = now or datetime.now(CST)
    if now.weekday() >= 5:
        return False
    return MORNING[1] < _minutes(now) < AFTERNOON[0]


def is_after_close(now=None):
    """Whether Shanghai time is on a weekday at/after the 15:00 close."""
    now = now or datetime.now(CST)
    return now.weekday() < 5 and _minutes(now) >= AFTERNOON[1]


def poll_plan(now, interval, off_hours_interval, midday_interval=None, trading_hours_only=True):
    """Decide whether this tick polls, and how long to sleep afterwards.

    Kept pure so the window boundaries are testable without waiting for a
    session. 盘中与午休同速，收盘后降速，开盘前与周末在 --trading-hours-only 下静默。
    """
    if in_trading_hours(now):
        return True, max(5.0, float(interval))
    if in_midday_break(now):
        return True, max(5.0, float(midday_interval if midday_interval else interval))
    slow = max(30.0, float(off_hours_interval))
    return (not trading_hours_only) or is_after_close(now), slow


def main():
    ap = argparse.ArgumentParser(description="爱投顾内参 → 飞书 中继")
    ap.add_argument("--once", action="store_true", help="拉一次增量并发送")
    ap.add_argument("--loop", action="store_true", help="循环轮询（兵底）")
    ap.add_argument("--interval", type=float, default=90, help="轮询间隔秒（默认90）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不发送")
    ap.add_argument("--bootstrap", action="store_true", help="把当前所有内参标记为已读（不发送），首次部署用")
    ap.add_argument("--trading-hours-only", action="store_true",
                    help="盘中与午休高频轮询，上海时间收盘后保留低频轮询")
    ap.add_argument("--off-hours-interval", type=float, default=600,
                    help="收盘后轮询间隔秒（默认600，即10分钟）")
    ap.add_argument("--midday-interval", type=float, default=None,
                    help="午间休市(11:30-13:00)轮询间隔秒，默认与 --interval 相同")
    ap.add_argument("--product", action="append", help="只处理指定 productId")
    args = ap.parse_args()

    prods = {p: WATCH.get(p, "内参") for p in args.product} if args.product else WATCH

    if args.bootstrap:
        deliver_new(products=prods, bootstrap=True)
        return 0
    if args.loop:
        print("itougu 内参兵底轮询启动 interval=%ss" % args.interval, flush=True)
        ensure_baseline(prods)
        while True:
            # Keep the remote API safety net alive across the midday break and
            # after the Shanghai close, but avoid restoring any local
            # WeChat-table listener path.
            should_poll, sleep_seconds = poll_plan(
                datetime.now(CST), interval=args.interval,
                off_hours_interval=args.off_hours_interval,
                midday_interval=args.midday_interval,
                trading_hours_only=args.trading_hours_only)
            if should_poll:
                try:
                    deliver_new(products=prods, dry_run=args.dry_run,
                                delivery_label=os.environ.get("ITOUGU_DELIVERY_LABEL", "轮询"))
                    # 已登记公开圈子走同一 Itougu API 的 view/list，绝不读
                    # 本地微信表；付费内参仍走 appendContent/list。
                    if not args.dry_run:
                        poll_public_views(verbose=True, delivery_label=os.environ.get("ITOUGU_DELIVERY_LABEL", "轮询"))
                except Exception as e:
                    print("轮询异常(忽略): %s" % e, flush=True)
            time.sleep(sleep_seconds)
    else:
        should_poll, _ = poll_plan(datetime.now(CST), interval=args.interval,
                                   off_hours_interval=args.off_hours_interval,
                                   midday_interval=args.midday_interval,
                                   trading_hours_only=args.trading_hours_only)
        if not should_poll:
            return 0   # 非监听时段静默跳过（避免定时任务日志刷屏）
        n = deliver_new(products=prods, dry_run=args.dry_run,
                        delivery_label=os.environ.get("ITOUGU_DELIVERY_LABEL", "轮询"))
        if n:
            print("完成，发送 %d 条" % n, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
