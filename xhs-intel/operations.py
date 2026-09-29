"""Controlled Spider_XHS operation boundary used by the Feishu command lane.

The edge process owns the authenticated Spider_XHS session.  Feishu sends only
JSON arguments and never receives cookies or signing material.  The operation
names intentionally mirror the public classes in Spider_XHS so adding an
upstream method is an explicit registry change rather than an arbitrary
Python attribute call from an untrusted message.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import asyncio
import uuid
from pathlib import Path
from typing import Any

from common import error_code


class OperationError(ValueError):
    """A user-correctable operation request error."""

PUBLIC_METHODS = {
    "pc": (
        "get_homefeed_all_channel", "get_homefeed_recommend", "get_homefeed_recommend_by_num",
        "get_user_info", "get_user_me", "get_user_note_info", "get_user_all_notes",
        "get_user_like_note_info", "get_user_all_like_note_info", "get_user_collect_note_info",
        "get_user_all_collect_note_info", "get_note_info", "share_code", "get_widgets",
        "worldcup_note_seo", "report_note_metrics", "report_history_web", "get_search_keyword",
        "get_web_config", "get_system_config", "get_trending_queries", "get_celestial_lt",
        "get_user_board", "get_dqa_recommend", "get_global_config", "get_worldcup_display_period",
        "get_worldcup_live_bar", "sync_search_history", "sync_search_history_captured",
        "search_onebox", "search_filter", "search_note", "search_some_note", "search_user",
        "search_some_user", "get_note_out_comment", "get_note_all_out_comment",
        "get_note_inner_comment", "get_note_all_inner_comment", "get_note_all_comment",
        "get_unread_message", "get_metions", "get_all_metions", "get_likesAndcollects",
        "get_all_likesAndcollects", "get_new_connections", "get_all_new_connections",
        "get_note_no_water_video", "get_note_no_water_img",
    ),
    "creator": (
        "get_user_info", "get_topic", "get_location_info", "get_fileIds", "get_file_ids",
        "upload_media", "query_transcode", "encryption", "post_note", "get_file_info",
        "extract_video_cover_and_metadata", "get_posted_notes_page", "get_publish_note_info",
        "get_all_posted_notes", "get_all_publish_note_info",
    ),
    "pgy": (
        "get_all_categories", "choose_categories", "get_track", "get_user_by_page", "get_some_user", "get_user_detail",
        "get_user_fans_detail", "get_user_fans_history", "get_user_notes_detail", "get_self_info",
        "get_self_info_signed", "send_invite",
    ),
    "qianfan": (
        "get_all_categories", "choose_categories", "get_user_by_page", "get_some_user", "get_user_detail",
        "get_user_cooperation", "get_user_shop", "get_user_item", "get_user_fans",
    ),
    "live": (
        "list_categories", "square_feed", "current_room_info", "join_room", "viewer_heart",
        "join_business_base_info", "user_card", "join_comment_info", "aggregate_business_info",
        "resource_by_id", "gift_panel", "charge_panel", "user_violation", "send_comment",
        "mic_relation", "get_chats", "get_group_chats", "get_chat_info", "get_message_history",
        "get_following", "get_unread", "get_unread_count", "get_web_config", "get_system_config",
        "get_user_me", "get_recent_chats", "get_emoji_config", "get_redmoji_version",
        "get_message_config", "voice_convert", "get_all_offline_messages", "report_offline_message_ack",
        "get_group_message_history", "revoke_message", "revoke_group_all_message", "report_total_unread",
        "get_message_location_list", "report_message_location_read", "add_personal_emoji",
        "delete_personal_emoji", "get_smile_file_id", "get_stick_top_messages", "delete_message",
        "send_captured_short_link_message", "send_short_link_message", "get_celestial_lt",
        "detect_message_policy", "mark_messages_read",
        "connect_push", "connect_push_from_storage",
        "watch", "events", "stop_watch",
    ),
}

# These routes can alter an account or send a message.  They remain available,
# but Feishu must include {"confirm":true} in the command payload.
MUTATING = {
    ("pc", "sync_search_history"), ("pc", "sync_search_history_captured"),
    ("pc", "report_note_metrics"), ("pc", "report_history_web"),
    ("creator", "upload_media"), ("creator", "post_note"),
    ("pgy", "send_invite"), ("live", "send_comment"), ("live", "revoke_message"),
    ("live", "revoke_group_all_message"), ("live", "report_offline_message_ack"),
    ("live", "report_total_unread"), ("live", "report_message_location_read"),
    ("live", "add_personal_emoji"), ("live", "delete_personal_emoji"),
    ("live", "delete_message"), ("live", "send_captured_short_link_message"),
    ("live", "send_short_link_message"), ("live", "mark_messages_read"),
}

SECRET_KEY_RE = re.compile(
    r"(?:cookie|token|authorization|password|secret|x-s-common|x-s|x-t|web_session|a1|gid|id_token)",
    re.I,
)
URL_SECRET_RE = re.compile(r"([?&](?:xsec_token|web_session|token|signature)=)[^&#\s]+", re.I)
ALLOWED_MEDIA_ROOT = Path(os.environ.get("XHS_MEDIA_ROOT", "/var/lib/xhs-collector/inbox")).resolve()


class LiveWatchManager:
    """Bounded background reader for the Spider_XHS push WebSocket.

    Feishu webhook execution is deliberately short-lived, while a live room
    stream is not.  A watch is therefore a fenced, in-memory edge session that
    can be polled with ``#xhs live.events <watch-id>``.  It has a hard duration
    and event cap so a forgotten Feishu command cannot create an unbounded
    worker.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.sessions: dict[str, dict[str, Any]] = {}

    def start(self, runtime, room_id: str, seconds: int = 60, max_events: int = 50, sid: str = ""):
        room_id = str(room_id or "").strip()
        if not room_id:
            raise OperationError("live.watch requires room_id")
        seconds = max(5, min(int(seconds), 600))
        max_events = max(1, min(int(max_events), 200))
        watch_id = "xhs-watch-" + uuid.uuid4().hex[:16]
        session = {"watch_id": watch_id, "room_id": room_id, "status": "starting",
                   "started_at": time.time(), "events": [], "error": None, "sid": str(sid or ""),
                   "stop": threading.Event()}
        with self.lock:
            self.sessions[watch_id] = session
        threading.Thread(target=self._run, args=(runtime, session, seconds, max_events),
                         name=f"xhs-watch-{watch_id[-6:]}", daemon=True).start()
        return {"watch_id": watch_id, "room_id": room_id, "status": "starting",
                "seconds": seconds, "max_events": max_events}

    def _run(self, runtime, session, seconds: int, max_events: int):
        async def consume():
            live = runtime._ensure_live()
            if session.get("sid"):
                client = await live.connect_push(sid=session["sid"], room_id=session["room_id"])
            else:
                client = await live.connect_push_from_storage(room_id=session["room_id"])
            session["client"] = client
            stream = client.events()
            deadline = time.monotonic() + seconds
            session["status"] = "running"
            try:
                while len(session["events"]) < max_events and not session["stop"].is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        event = await asyncio.wait_for(stream.__anext__(), timeout=min(remaining, 2.0))
                    except asyncio.TimeoutError:
                        continue
                    session["events"].append(sanitize(event))
            finally:
                await client.close()

        try:
            asyncio.run(consume())
            session["status"] = "stopped" if session["stop"].is_set() else "completed"
        except Exception as exc:  # noqa: BLE001
            session["status"] = "failed"
            session["error"] = error_code(exc)
        finally:
            session.pop("client", None)

    def get(self, watch_id: str):
        with self.lock:
            session = self.sessions.get(str(watch_id))
            if not session:
                raise OperationError("unknown live watch id")
            return {key: value for key, value in session.items() if key not in {"stop", "client"}}

    def stop(self, watch_id: str):
        with self.lock:
            session = self.sessions.get(str(watch_id))
            if not session:
                raise OperationError("unknown live watch id")
            session["stop"].set()
            return {"watch_id": str(watch_id), "status": "stop_requested"}


LIVE_WATCHES = LiveWatchManager()


def _cookie_map(auth) -> dict[str, str]:
    profile = getattr(auth, "profile", None)
    values = getattr(profile, "cookie_map", None) or {}
    return {str(key): str(value) for key, value in dict(values).items()}


def _safe_media_path(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise OperationError("media paths must be non-empty strings")
    path = Path(value).expanduser().resolve()
    if path != ALLOWED_MEDIA_ROOT and ALLOWED_MEDIA_ROOT not in path.parents:
        raise OperationError("media path must be under the configured XHS media inbox")
    if not path.is_file():
        raise OperationError("media file is not present in the XHS media inbox")
    return str(path)


def _validate_media(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [_validate_media(item) for item in value]
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            if key in {"path", "images", "video", "media"}:
                if key == "path":
                    output[key] = _safe_media_path(item)
                else:
                    output[key] = _validate_media(item)
            else:
                output[key] = _validate_media(item)
        return output
    return value


def _redact(value: Any, key: str = "") -> Any:
    if SECRET_KEY_RE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, tuple):
        return [_redact(item, key) for item in value]
    if isinstance(value, str):
        return URL_SECRET_RE.sub(r"\1[REDACTED]", value)
    return value


def sanitize(value: Any) -> Any:
    """Make upstream JSON safe to place in a Feishu message."""
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        value = str(value)
    return _redact(value)


def format_result(operation: str, result: Any, *, limit: int = 20000) -> str:
    safe = sanitize(result)
    if isinstance(safe, str):
        text = safe
    else:
        text = json.dumps(safe, ensure_ascii=False, indent=2, default=str)
    if len(text) > limit:
        text = text[:limit] + "\n…（结果已截断，可用 page/cursor 分页或降低 limit）"
    return f"小红书操作：{operation}\n{text}"


class SpiderRuntime:
    """Lazy, process-local authenticated API clients."""

    def __init__(self, source_root: Path, cookie_file: Path):
        self.source_root = Path(source_root)
        self.cookie_file = Path(cookie_file)
        self.lock = threading.RLock()
        self.auth = None
        self.pc = None
        self.creator = None
        self.live = None
        self.pgy = None
        self.qianfan = None
        self._cookie_values: dict[str, str] = {}

    def _ensure_auth(self):
        if self.auth is not None:
            return self.auth
        cookie = self.cookie_file.read_text(encoding="utf-8").strip()
        fields = {item.split("=", 1)[0].strip() for item in cookie.split(";") if "=" in item}
        if not {"a1", "web_session"}.issubset(fields):
            raise OperationError("XHS cookie is missing a1 or web_session")
        if str(self.source_root) not in os.sys.path:
            os.sys.path.insert(0, str(self.source_root))
        from xhs_utils.xhs_auth import XHSUnifiedAuth
        self.auth = XHSUnifiedAuth.from_cookie(cookie)
        self._cookie_values = _cookie_map(self.auth.pc)
        return self.auth

    def _ensure_pc(self):
        self._ensure_auth()
        if self.pc is None:
            from apis.xhs_pc_apis import XHS_Apis
            self.pc = XHS_Apis(self.auth.pc).bootstrap()
        return self.pc

    def _ensure_creator(self):
        self._ensure_auth()
        if self.creator is None:
            from apis.xhs_creator_apis import XHS_Creator_Apis
            self.creator = XHS_Creator_Apis(self.auth.creator).bootstrap()
        return self.creator

    def _ensure_live(self):
        self._ensure_auth()
        if self.live is None:
            from apis.xhs_live import XHSLiveAPI
            self.live = XHSLiveAPI(self.auth.pc)
        return self.live

    def _ensure_pgy(self):
        self._ensure_auth()
        if self.pgy is None:
            from apis.xhs_pugongying_apis import PuGongYingAPI
            self.pgy = PuGongYingAPI()
        return self.pgy

    def _ensure_qianfan(self):
        self._ensure_auth()
        if self.qianfan is None:
            from apis.xhs_qianfan_apis import QianFanAPI
            self.qianfan = QianFanAPI()
        return self.qianfan

    def _target(self, namespace: str):
        if namespace == "pc":
            return self._ensure_pc(), None
        if namespace == "creator":
            return self._ensure_creator(), None
        if namespace == "live":
            return self._ensure_live(), None
        if namespace == "pgy":
            return self._ensure_pgy(), self._cookie_values
        if namespace == "qianfan":
            return self._ensure_qianfan(), self._cookie_values
        raise OperationError(f"unknown Spider_XHS namespace: {namespace}")

    def execute(self, namespace: str, method: str, args=None, kwargs=None) -> Any:
        namespace, method = str(namespace).lower().strip(), str(method).strip()
        if method not in PUBLIC_METHODS.get(namespace, ()):
            raise OperationError(f"unsupported Spider_XHS operation: {namespace}.{method}")
        args = list(args or [])
        kwargs = dict(kwargs or {})
        confirm = bool(kwargs.pop("confirm", False))
        if (namespace, method) in MUTATING and not confirm:
            raise OperationError(f"{namespace}.{method} changes account state; add {{\"confirm\":true}}")
        if namespace in {"pgy", "qianfan"} and method == "choose_categories":
            # Spider_XHS implements this as an interactive terminal picker.
            # Feishu has no stdin, so preserve the capability as a safe
            # category listing and let the caller pass the selected values to
            # the subsequent API operation.
            target, injected = self._target(namespace)
            return {
                "interactive": False,
                "categories": target.get_all_categories(injected),
                "message": "Feishu cannot open the terminal selector; use the returned category data in the next call.",
            }
        if namespace == "live" and method in {"watch", "connect_push", "connect_push_from_storage"}:
            sid = kwargs.pop("sid", "")
            if method == "connect_push":
                sid = sid or (args[0] if args else "")
                room_id = kwargs.pop("room_id", args[1] if len(args) > 1 else "")
                seconds = kwargs.pop("seconds", 60)
            else:
                room_id = args[0] if args else kwargs.pop("room_id", "")
                seconds = args[1] if len(args) > 1 else kwargs.pop("seconds", 60)
            max_events = kwargs.pop("max_events", 50)
            # Initialize the authenticated live API before starting the
            # background loop so missing/expired Cookie errors are immediate.
            self._ensure_live()
            return LIVE_WATCHES.start(self, room_id, seconds, max_events, sid=sid)
        if namespace == "live" and method == "events":
            watch_id = args[0] if args else kwargs.pop("watch_id", "")
            return LIVE_WATCHES.get(watch_id)
        if namespace == "live" and method == "stop_watch":
            watch_id = args[0] if args else kwargs.pop("watch_id", "")
            return LIVE_WATCHES.stop(watch_id)
        # File input is intentionally limited to a private edge inbox.  This
        # prevents a Feishu message from turning the API into an arbitrary file
        # reader while still allowing staged creator uploads.
        args = [_validate_media(item) for item in args]
        kwargs = _validate_media(kwargs)
        if namespace == "creator" and method in {"upload_media", "get_file_info", "extract_video_cover_and_metadata"} and args:
            media_path = _safe_media_path(args[0])
            # upload_media/post_note accept a path and load it themselves;
            # the lower-level metadata helpers accept bytes instead.
            args[0] = media_path if method == "upload_media" else Path(media_path).read_bytes()
        if namespace == "creator" and method == "post_note" and args and isinstance(args[0], dict):
            note = dict(args[0])
            if "images" in note:
                note["images"] = [_safe_media_path(item) for item in note["images"]]
            if "video" in note:
                note["video"] = _safe_media_path(note["video"])
            args[0] = note
        with self.lock:
            target, injected = self._target(namespace)
            fn = getattr(target, method, None)
            if not callable(fn):
                raise OperationError(f"Spider_XHS method is unavailable: {namespace}.{method}")
            if injected is not None:
                # Pgy/Qianfan APIs take cookies explicitly in every public call.
                kwargs["cookies"] = injected
            return fn(*args, **kwargs)

    def close(self):
        with self.lock:
            if self.auth is not None:
                try:
                    self.auth.close()
                except Exception:
                    pass
            self.auth = self.pc = self.creator = self.live = self.pgy = self.qianfan = None


def parse_operation_payload(payload: Any) -> tuple[str, str, list[Any], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise OperationError("operation payload must be an object")
    target = str(payload.get("operation") or payload.get("method") or "").strip()
    if "." not in target:
        raise OperationError("operation must be namespace.method")
    namespace, method = target.split(".", 1)
    args = payload.get("args", [])
    kwargs = payload.get("kwargs", {})
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        raise OperationError("args must be a list and kwargs must be an object")
    return namespace, method, args, kwargs


def capability_text() -> str:
    lines = [
        "Spider_XHS Feishu 能力（统一命令格式：#xhs api namespace.method {\"args\":[],\"kwargs\":{}}）",
        "读取操作可直接执行；发布、私信、撤回、邀请等写操作必须在 kwargs 中带 confirm=true。",
        "Creator 上传使用 edge 的 XHS_MEDIA_ROOT 私有目录，默认 /var/lib/xhs-collector/inbox。",
    ]
    for namespace, methods in PUBLIC_METHODS.items():
        lines.append(f"{namespace}: " + "、".join(methods))
    lines.extend([
        "常用别名：#xhs search 关键词 [数量]；#xhs note 笔记URL；#xhs user 用户ID；",
        "#xhs comments 笔记URL；#xhs feed 频道 [数量]；#xhs unread；#xhs latest；#xhs status。",
        "直播事件：#xhs live.watch 房间ID [秒数]；#xhs live.events watch_id。",
    ])
    return "\n".join(lines)


def parse_json_tail(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OperationError(f"JSON 参数无效：{exc.msg}") from exc
    if not isinstance(value, dict):
        raise OperationError("JSON 参数必须是对象")
    return value


def build_alias(text: str) -> tuple[str, str, list[Any], dict[str, Any]] | None:
    """Translate concise group commands into the stable operation contract."""
    parts = text.strip().split(None, 2)
    if not parts:
        return None
    command = parts[0].lower()
    if command in {"search", "搜索"} and len(parts) >= 2:
        query = parts[1]
        count = 5
        if len(parts) >= 3:
            tail = parts[2].strip()
            if tail.isdigit():
                count = int(tail)
            else:
                match = re.fullmatch(r"(.+?)\s+(\d{1,2})", tail)
                if match:
                    query += " " + match.group(1)
                    count = int(match.group(2))
                else:
                    query += " " + tail
        return "pc", "search_some_note", [query, max(1, min(count, 20))], {}
    if command in {"note", "笔记"} and len(parts) >= 2:
        return "pc", "get_note_info", [parts[1]], {}
    if command in {"user", "用户"} and len(parts) >= 2:
        return "pc", "get_user_info", [parts[1]], {}
    if command in {"comments", "comment", "评论"} and len(parts) >= 2:
        return "pc", "get_note_all_comment", [parts[1]], {}
    if command in {"feed", "推荐"}:
        category = parts[1] if len(parts) >= 2 else "homefeed"
        count = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 5
        return "pc", "get_homefeed_recommend_by_num", [category, max(1, min(count, 20))], {}
    if command in {"unread", "未读"}:
        return "pc", "get_unread_message", [], {}
    return None


def command_to_operation(command: str) -> tuple[str, str, list[Any], dict[str, Any]] | None:
    text = str(command or "").strip()
    if text.lower().startswith("#xhs"):
        text = text[4:].strip()
    if not text or text.lower() in {"help", "帮助"}:
        return None
    alias = build_alias(text)
    if alias:
        return alias
    head, sep, tail = text.partition(" ")
    if head.lower() in {"api", "call", "调用"}:
        target, sep2, json_tail = tail.strip().partition(" ")
        if not sep2:
            raise OperationError("用法：#xhs api namespace.method {\"args\":[],\"kwargs\":{}}")
        payload = parse_json_tail(json_tail.strip())
        payload["operation"] = target
        return parse_operation_payload(payload)
    if head.lower() in {"live.watch", "live.connect_push", "live.connect_push_from_storage"}:
        values = tail.strip().split() if sep else []
        if not values:
            raise OperationError("用法：#xhs live.watch 房间ID [秒数]")
        if head.lower() == "live.connect_push":
            return "live", "connect_push", [values[0], values[1]] if len(values) > 1 else [values[0]], {}
        return "live", head.split(".", 1)[1], [values[0], int(values[1]) if len(values) > 1 and values[1].isdigit() else 60], {}
    if head.lower() in {"live.events", "live.stop_watch"}:
        values = tail.strip().split() if sep else []
        if not values:
            raise OperationError("用法：#xhs live.events watch_id")
        return "live", head.split(".", 1)[1], [values[0]], {}
    if "." in head:
        payload = parse_json_tail(tail.strip()) if sep and tail.strip() else {}
        payload["operation"] = head
        return parse_operation_payload(payload)
    return None


__all__ = [
    "PUBLIC_METHODS", "MUTATING", "OperationError", "SpiderRuntime", "sanitize",
    "format_result", "parse_operation_payload", "capability_text", "command_to_operation",
]
