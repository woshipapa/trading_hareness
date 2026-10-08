"""Bounded Spider_XHS PC adapter; login/transport errors never become empty success."""
import datetime as dt
import re
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from common import digest, error_code


class InvalidNoteLink(ValueError):
    """The submitted text does not contain a supported XHS note link."""


class NoteFetchUnavailable(RuntimeError):
    """The note needs a fresh share link and is not available in the local cache."""


_URL_RE = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
_NOTE_PATH_RE = re.compile(r'/(?:explore|discovery/item)/([0-9a-fA-F]{24})(?:/|$)')
_TRAILING_SHARE_TEXT = '.,;!?)]}\u3002\uff0c\uff1b\uff01\uff1f\u3011\u300b'
_MAX_NOTE_IMAGES = 18
_IMAGE_HOST_SUFFIXES = ('.xhscdn.com', '.xiaohongshu.com')
# A 50-note recommendation can spend several minutes in classification and
# local AI summarization. Keep signed links in memory for the same workday
# window, while never writing token material to SQLite, logs, or artifacts.
_EPHEMERAL_LINK_TTL = 6 * 60 * 60
_EPHEMERAL_LINKS = {}
_EPHEMERAL_LINKS_LOCK = threading.Lock()


def remember_note_link(note_id, fetch_url, *, ttl=_EPHEMERAL_LINK_TTL):
    """Keep a fresh signed link in memory only; never persist xsec material."""
    note_id = str(note_id or '').lower()
    parsed = urlparse(str(fetch_url or ''))
    token = parse_qs(parsed.query).get('xsec_token', [''])[0]
    source = parse_qs(parsed.query).get('xsec_source', ['pc_share'])[0]
    if not re.fullmatch(r'[a-f0-9]{24}', note_id) or not token:
        return False
    with _EPHEMERAL_LINKS_LOCK:
        now = time.time()
        for key, value in list(_EPHEMERAL_LINKS.items()):
            if value[2] <= now:
                _EPHEMERAL_LINKS.pop(key, None)
        _EPHEMERAL_LINKS[note_id] = (token[:2048], source[:64], now + max(30, int(ttl)))
    return True


def ephemeral_note_link(note_id):
    """Return a fresh signed XHS URL, if the collector saw one recently."""
    note_id = str(note_id or '').lower()
    with _EPHEMERAL_LINKS_LOCK:
        value = _EPHEMERAL_LINKS.get(note_id)
        if not value:
            return ''
        token, source, expires = value
        if expires <= time.time():
            _EPHEMERAL_LINKS.pop(note_id, None)
            return ''
    return f'https://www.xiaohongshu.com/explore/{note_id}?' + urlencode({
        'xsec_token': token,
        'xsec_source': source if re.fullmatch(r'[A-Za-z0-9_-]+', source) else 'pc_share',
    })


def _image_source_urls(image):
    """Yield the image variants exposed by Spider_XHS without trusting arbitrary URLs."""
    if not isinstance(image, dict):
        return
    for key in ('url_default', 'url', 'url_pre', 'url_placeholder'):
        value = image.get(key)
        if isinstance(value, str) and value.strip():
            yield value.strip()
    for item in image.get('info_list') or []:
        if not isinstance(item, dict):
            continue
        value = item.get('url')
        if isinstance(value, str) and value.strip():
            yield value.strip()


def _canonical_image_url(value):
    """Turn an XHS image variant into a stable, no-watermark JPEG URL.

    The upstream image URLs contain size/format suffixes and sometimes expiring
    query parameters.  Only XHS-owned hosts are accepted and the canonical
    ``ci.xiaohongshu.com`` form keeps the durable note revision free of those
    transient parameters.
    """
    parsed = urlparse(str(value or '').strip())
    host = (parsed.hostname or '').lower().rstrip('.')
    if parsed.scheme not in {'http', 'https'} or not (host == 'xiaohongshu.com' or host.endswith(_IMAGE_HOST_SUFFIXES)):
        return ''
    if parsed.port not in (None, 80, 443):
        return ''
    path = parsed.path.split('!', 1)[0].split('?', 1)[0].strip('/')
    if not path:
        return ''
    if 'notes_pre_post/' in path:
        token = 'notes_pre_post/' + path.split('notes_pre_post/', 1)[1]
    elif 'spectrum' in path:
        token = '/'.join(path.split('/')[-2:])
    elif path.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
        token = '/'.join(path.split('/')[-3:])
    else:
        token = path.split('/')[-1]
    token = token.strip('/')
    if not token or len(token) > 512 or any(char in token for char in ('\x00', '\r', '\n')):
        return ''
    return f'https://ci.xiaohongshu.com/{token}?imageView2/format/jpeg'


def _extract_image_urls(images):
    urls = []
    seen = set()
    for image in images or []:
        selected = ''
        for candidate in _image_source_urls(image):
            selected = _canonical_image_url(candidate)
            if selected:
                break
        if selected and selected not in seen:
            seen.add(selected)
            urls.append(selected)
        if len(urls) >= _MAX_NOTE_IMAGES:
            break
    return urls


def _link_kind(url):
    parsed = urlparse(str(url or '').strip())
    host = (parsed.hostname or '').lower().rstrip('.')
    if parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password:
        raise InvalidNoteLink('invalid_xhs_url')
    try:
        port = parsed.port
    except ValueError as exc:
        raise InvalidNoteLink('invalid_xhs_url') from exc
    if port not in (None, 80, 443):
        raise InvalidNoteLink('invalid_xhs_url')
    if host == 'xiaohongshu.com' or host.endswith('.xiaohongshu.com'):
        return 'note'
    if host == 'xhslink.com' or host.endswith('.xhslink.com'):
        return 'short'
    raise InvalidNoteLink('unsupported_xhs_host')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


def resolve_xhs_short_url(url, *, max_redirects=5, timeout=10):
    """Resolve an XHS short link while validating every redirect target."""
    current = str(url)
    opener = build_opener(_NoRedirect())
    for _ in range(max_redirects + 1):
        kind = _link_kind(current)
        if kind == 'note':
            return current
        request = Request(current, headers={'User-Agent': 'Mozilla/5.0'}, method='GET')
        try:
            response = opener.open(request, timeout=timeout)
        except HTTPError as exc:
            try:
                if exc.code not in {301, 302, 303, 307, 308}:
                    raise RuntimeError('xhs_short_link_failed') from exc
                location = exc.headers.get('Location')
            finally:
                exc.close()
        else:
            try:
                location = response.headers.get('Location')
            finally:
                response.close()
        if not location:
            raise InvalidNoteLink('xhs_short_link_missing_redirect')
        current = urljoin(current, location)
        _link_kind(current)
    raise InvalidNoteLink('xhs_short_link_too_many_redirects')


def parse_note_reference(value, *, short_resolver=resolve_xhs_short_url):
    """Extract one note URL from pasted link/share text and build a fetch-only URL."""
    match = _URL_RE.search(str(value or '').strip())
    if not match:
        raise InvalidNoteLink('xhs_note_url_required')
    submitted = match.group(0).rstrip(_TRAILING_SHARE_TEXT)
    kind = _link_kind(submitted)
    resolved = short_resolver(submitted) if kind == 'short' else submitted
    if _link_kind(resolved) != 'note':
        raise InvalidNoteLink('xhs_note_url_required')
    parsed = urlparse(resolved)
    path_match = _NOTE_PATH_RE.search(parsed.path)
    if not path_match:
        raise InvalidNoteLink('xhs_note_id_required')
    note_id = path_match.group(1).lower()
    query = parse_qs(parsed.query)
    fetch_params = {}
    token = str((query.get('xsec_token') or [''])[0])[:2048]
    source = str((query.get('xsec_source') or ['pc_share'])[0])[:64]
    if token:
        fetch_params['xsec_token'] = token
    fetch_params['xsec_source'] = source if re.fullmatch(r'[A-Za-z0-9_-]+', source) else 'pc_share'
    fetch_url = f'https://www.xiaohongshu.com/explore/{note_id}?{urlencode(fetch_params)}'
    return {'note_id': note_id, 'fetch_url': fetch_url, 'has_xsec_token': bool(token)}


def collect_single_note(store, source_root, cookie_file, value, *, deliver_to_feishu=False,
                        short_resolver=resolve_xhs_short_url):
    """Fetch one submitted note and queue an idempotent local AI analysis."""
    reference = parse_note_reference(value, short_resolver=short_resolver)
    api = _pc_api(source_root, cookie_file)
    ok, _message, response = api.get_note_info(reference['fetch_url'])
    items = ((response or {}).get('data') or {}).get('items') or []
    note_id = reference['note_id']
    if ok and items and isinstance(items[0], dict):
        note = normalize({'id': note_id}, f'single:{note_id}', items[0])
        remember_note_link(note_id, reference['fetch_url'])
        fetch_source = 'live'
    else:
        note = store.latest_note(note_id)
        if not note:
            message = ('note_fetch_failed_use_fresh_share_link'
                       if not reference['has_xsec_token'] else 'note_fetch_failed')
            raise NoteFetchUnavailable(message)
        fetch_source = 'cache'
    return store.enqueue_single_note(
        note, deliver_to_feishu=deliver_to_feishu, fetch_source=fetch_source)


def normalize(item, query, detail=None):
    card = (detail or {}).get('note_card') or item.get('note_card') or item
    note_id = str(item.get('id') or item.get('note_id') or card.get('note_id') or '')
    if not re.fullmatch(r'[a-fA-F0-9]{24}', note_id):
        raise ValueError('invalid_note_id')
    stamp = card.get('time')
    published = None
    if isinstance(stamp, (int, float)):
        published = dt.datetime.fromtimestamp(stamp / 1000 if stamp > 1e11 else stamp, dt.timezone.utc).isoformat()
    images = card.get('image_list') or []
    image_urls = _extract_image_urls(images)
    # Stable image identity, excluding expiring CDN parameters and engagement.
    image_ids = [str(x.get('file_id') or '') for x in images if isinstance(x, dict) and x.get('file_id')]
    if not image_ids:
        image_ids = [url.rsplit('/', 1)[-1].split('?', 1)[0] for url in image_urls]
    content = {'title': str(card.get('title') or card.get('display_title') or ''),
               'text': str(card.get('desc') or ''), 'image_ids': image_ids,
               'image_urls': image_urls,
               'video_id': str((card.get('video') or {}).get('consumer', {}).get('origin_video_key') or '')}
    author = card.get('user') or {}
    return {'note_id': note_id, **content, 'author': str(author.get('nickname') or ''),
            'author_id': str(author.get('user_id') or author.get('userId') or ''),
            'published_at': published, 'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'url': f'https://www.xiaohongshu.com/explore/{note_id}', 'query': query,
            'content_hash': digest(content),
            'coverage': 'note_text_and_image_refs' if image_urls else 'note_text_only',
            'image_count': len(image_urls), 'media_analyzed': False,
            'detail_fetched': detail is not None}


def _pc_api(source_root, cookie_file):
    """Create one authenticated PC API client without leaking Cookie material."""
    cookie = Path(cookie_file).read_text().strip() if Path(cookie_file).is_file() else ''
    if not cookie:
        raise RuntimeError('missing_cookie')
    sys.path.insert(0, str(source_root))
    from loguru import logger
    logger.remove()
    from apis.xhs_pc_apis import XHS_Apis
    from xhs_utils.xhs_pc import XHSPcAuth
    return XHS_Apis(XHSPcAuth.from_cookie(cookie)).bootstrap()


def collect_watched(store, source_root, cookie_file, watch_users, *, limit=8,
                    request_key=None, delay=3):
    """Poll the first page of each configured user's posted notes.

    XHS does not expose a reliable push event for ordinary note publication, so
    this lane is intentionally bounded polling.  The durable note revision
    table makes repeated observations idempotent and the caller supplies a
    time-bucketed request key so each user can be checked again on the next
    interval.
    """
    users = [dict(item) for item in (watch_users or []) if isinstance(item, dict)]
    if not users:
        return {'status': 'idle', 'reason': 'no_watch_users', 'users': 0}
    day = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    key_prefix = str(request_key or day)
    cookie = Path(cookie_file).read_text().strip() if Path(cookie_file).is_file() else ''
    if not cookie:
        for user in users:
            user_id = str(user.get('user_id') or '').strip()
            key = f'{key_prefix}:{digest("watch:" + user_id)[:12]}'
            if user_id and store.start_run(key, f'watch:{user_id}'):
                store.end_run(key, 'blocked', 0, 0, 'missing_cookie')
        return {'status': 'blocked', 'reason': 'missing_cookie'}

    api = None
    results = []
    for user in users:
        user_id = str(user.get('user_id') or '').strip()
        label = str(user.get('label') or '').strip()
        if not user_id:
            continue
        query = f'watch:{label or user_id}'
        key = f'{key_prefix}:{digest("watch:" + user_id)[:12]}'
        if not store.start_run(key, query):
            continue
        fetched = added = 0
        try:
            if api is None:
                api = _pc_api(source_root, cookie_file)
            # One page only.  Fetching all historical posts would make a
            # periodic watcher expensive and would defeat the note deduper.
            ok, _message, body = api.get_user_note_info(user_id, '', '', 'pc_user')
            if not ok:
                raise RuntimeError('user_notes_failed')
            data = (body or {}).get('data') or {}
            items = data.get('notes') or data.get('items') or []
            if not isinstance(items, list):
                raise ValueError('invalid_user_notes_response')
            for item in items[:max(1, min(int(limit), 20))]:
                if not isinstance(item, dict):
                    continue
                note_id = str(item.get('id') or item.get('note_id') or '')
                if not re.fullmatch(r'[0-9a-fA-F]{24}', note_id):
                    continue
                token = item.get('xsec_token') or item.get('xsecToken') or ''
                detail = None
                if token:
                    remember_note_link(note_id, f'https://www.xiaohongshu.com/explore/{note_id}?'
                                       + urlencode({'xsec_token': token, 'xsec_source': 'pc_user'}))
                    time.sleep(delay)
                    ok, _msg, detail_body = api.get_note_info(
                        f'https://www.xiaohongshu.com/explore/{note_id}?'
                        + urlencode({'xsec_token': token, 'xsec_source': 'pc_user'}))
                    if not ok:
                        raise RuntimeError('detail_failed')
                    detail = ((detail_body or {}).get('data') or {}).get('items', [None])[0]
                note = normalize(item, query, detail)
                note['watch_user_id'] = user_id
                note['watch_user_label'] = label
                fetched += 1
                added += store.add_note(note, query)
            store.end_run(key, 'completed', fetched, added)
            results.append({'user_id': user_id, 'label': label, 'fetched': fetched, 'added': added})
        except Exception as exc:
            store.end_run(key, 'partial' if fetched else 'failed', fetched, added, error_code(exc))
            return {'status': 'partial' if fetched else 'failed', 'users': results,
                    'error': error_code(exc)}
        finally:
            while store.enqueue_pending():
                pass
        time.sleep(delay)
    return {'status': 'completed', 'users': results}


def collect(store, source_root, cookie_file, queries, *, limit=8, request_key=None, delay=3):
    day = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    results = []
    cookie = Path(cookie_file).read_text().strip() if Path(cookie_file).is_file() else ''
    if not cookie:
        for query in queries:
            key = request_key + ':' + digest(query)[:12] if request_key else day + ':' + digest(query)[:12]
            if store.start_run(key, query):
                store.end_run(key, 'blocked', 0, 0, 'missing_cookie')
        return {'status': 'blocked', 'reason': 'missing_cookie'}
    sys.path.insert(0, str(source_root))
    from loguru import logger
    # Upstream logger.exception includes local variables/URLs. Persist our own
    # sanitized observations instead; cookies and signed URLs never reach logs.
    logger.remove()
    from apis.xhs_pc_apis import XHS_Apis
    from xhs_utils.xhs_pc import XHSPcAuth
    api = None
    for query in queries:
        key = request_key + ':' + digest(query)[:12] if request_key else day + ':' + digest(query)[:12]
        if not store.start_run(key, query):
            continue
        fetched = added = 0
        try:
            if api is None:
                api = XHS_Apis(XHSPcAuth.from_cookie(cookie)).bootstrap()
            # Exactly one bounded page; never follow an unbounded pagination loop.
            ok, _message, body = api.search_note(query, page=1, sort_type_choice=1, note_time=1)
            if not ok:
                raise RuntimeError('search_failed')
            items = body.get('data', {}).get('items')
            if not isinstance(items, list):
                raise ValueError('invalid_search_response')
            for item in items[:limit]:
                if item.get('model_type') not in (None, 'note'):
                    continue
                note_id = str(item.get('id', ''))
                if not re.fullmatch(r'[0-9a-fA-F]{24}', note_id):
                    continue
                token = item.get('xsec_token')
                if not token:
                    raise ValueError('missing_detail_token')
                remember_note_link(note_id, f'https://www.xiaohongshu.com/explore/{note_id}?'
                                   + urlencode({'xsec_token': token, 'xsec_source': 'pc_search'}))
                time.sleep(delay)
                ok, _msg, detail = api.get_note_info(f'https://www.xiaohongshu.com/explore/{note_id}?' + urlencode({'xsec_token': token, 'xsec_source': 'pc_search'}))
                if not ok:
                    raise RuntimeError('detail_failed')
                note = normalize(item, query, detail['data']['items'][0])
                fetched += 1
                added += store.add_note(note, query)
            store.end_run(key, 'completed', fetched, added)
            results.append({'query': query, 'fetched': fetched, 'added': added})
        except Exception as exc:
            store.end_run(key, 'partial' if fetched else 'failed', fetched, added, error_code(exc))
            # Stop the entire batch on session/risk-control errors.
            return {'status': 'partial' if fetched else 'failed', 'queries': results, 'error': error_code(exc)}
        finally:
            while store.enqueue_pending():
                pass
        time.sleep(delay)
    return {'status': 'completed', 'queries': results}


def collect_recommendations(store, source_root, cookie_file, *, run_id, limit=50,
                            category='homefeed_recommend', delay=3):
    """Collect one bounded recommendation sweep.

    Spider_XHS internally pages the homefeed endpoint.  The caller gives us a
    hard item limit and this function never follows a caller-controlled cursor.
    Recommendation notes are persisted in their own run/items tables and are
    deliberately not put into the legacy global summary queue.
    """
    limit = max(1, min(int(limit), 50))
    cookie = Path(cookie_file).read_text().strip() if Path(cookie_file).is_file() else ''
    if not cookie:
        store.finish_recommendation_collection(run_id, 0, 0, 'missing_cookie')
        return {'status': 'blocked', 'reason': 'missing_cookie', 'run_id': run_id, 'fetched': 0, 'added': 0}
    api = None
    fetched = added = 0
    try:
        api = _pc_api(source_root, cookie_file)
        ok, message, items = api.get_homefeed_recommend_by_num(category, limit)
        if not ok or not isinstance(items, list):
            raise RuntimeError('recommendation_fetch_failed')
        query = f'recommendation:{run_id}'
        for rank, item in enumerate(items[:limit], 1):
            if not isinstance(item, dict) or item.get('model_type') not in (None, 'note'):
                continue
            note_id = str(item.get('id') or item.get('note_id') or '')
            if not re.fullmatch(r'[0-9a-fA-F]{24}', note_id):
                continue
            token = item.get('xsec_token') or item.get('xsecToken') or ''
            if not token:
                continue
            remember_note_link(note_id, f'https://www.xiaohongshu.com/explore/{note_id}?'
                               + urlencode({'xsec_token': token, 'xsec_source': 'pc_homefeed'}))
            time.sleep(delay)
            ok, _msg, detail_body = api.get_note_info(
                f'https://www.xiaohongshu.com/explore/{note_id}?' +
                urlencode({'xsec_token': token, 'xsec_source': 'pc_homefeed'}))
            if not ok:
                continue
            data = (detail_body or {}).get('data') or {}
            detail_items = data.get('items') or []
            detail = detail_items[0] if detail_items else None
            note = normalize(item, query, detail)
            note['source_kind'] = 'homefeed_recommendation'
            note['source_rank'] = rank
            note['recommendation_run_id'] = run_id
            was_added = store.add_note(note, query)
            revision, item_added = store.add_recommendation_item(run_id, note, rank)
            fetched += 1
            added += int(was_added or item_added)
        store.finish_recommendation_collection(run_id, fetched, added)
        return {'status': 'completed', 'run_id': run_id, 'category': category,
                'requested': limit, 'fetched': fetched, 'added': added}
    except Exception as exc:
        store.finish_recommendation_collection(run_id, fetched, added, error_code(exc))
        return {'status': 'partial' if fetched else 'failed', 'run_id': run_id,
                'requested': limit, 'fetched': fetched, 'added': added,
                'error': error_code(exc)}
