"""Bounded Spider_XHS PC adapter; login/transport errors never become empty success."""
import datetime as dt
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

from common import digest, error_code


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
    # Stable image identity, excluding expiring CDN parameters and engagement.
    image_ids = [str(x.get('file_id') or x.get('url_default', '').split('?')[0]) for x in images if isinstance(x, dict)]
    content = {'title': str(card.get('title') or card.get('display_title') or ''),
               'text': str(card.get('desc') or ''), 'image_ids': image_ids,
               'video_id': str((card.get('video') or {}).get('consumer', {}).get('origin_video_key') or '')}
    return {'note_id': note_id, **content, 'author': str((card.get('user') or {}).get('nickname') or ''),
            'published_at': published, 'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'url': f'https://www.xiaohongshu.com/explore/{note_id}', 'query': query,
            'content_hash': digest(content), 'coverage': 'note_text_only',
            'media_analyzed': False, 'detail_fetched': detail is not None}


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
