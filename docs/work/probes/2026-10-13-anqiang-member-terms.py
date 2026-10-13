"""W010 step 0, Read-only: the people of one group, the aliases the account gave them, their join history, and the account's one-to-one chats with them.
Everything goes to stdout as one JSON document; nothing is written on the host.
Prints shapes only; an invite token is masked."""
import json, os, re, sys, uuid
sys.path.insert(0, '/opt/larkagentx/source')
pid = next(p for p in os.listdir('/proc') if p.isdigit() and b'bridge/bridge.py' in open(f'/proc/{p}/cmdline', 'rb').read())
for item in open(f'/proc/{pid}/environ', 'rb').read().split(b'\0'):
    if item.startswith(b'LARKX_') or item.startswith(b'HOME='):
        key, _, value = item.decode().partition('=')
        os.environ.setdefault(key, value)
import requests
from protobuf_to_dict import protobuf_to_dict
from larkx.auth import LarkAuth
from larkx.proto import proto_pb2 as P, lark_all_pb2 as L, builders

auth = LarkAuth(); assert auth.load(), 'no credentials'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36'

def mask(value, depth=0):
    if isinstance(value, dict): return {k: mask(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list): return [mask(v, depth + 1) for v in value[:40]] + (['…+%d' % (len(value) - 40)] if len(value) > 40 else [])
    if isinstance(value, bytes): return '<%d bytes>' % len(value)
    if isinstance(value, str):
        value = re.sub(r'(hook/)[A-Za-z0-9-]{6,}', lambda m: m.group(1) + '****', value)
        return value if len(value) <= 90 else value[:90] + '…'
    return value


from urllib.parse import urlsplit, parse_qsl
from larkx.proto import gateway as G
from larkx.proto.ids import generate_long_request_id

def api(name, data):
    cmd, req_cls, resp_cls = G.resolve_cmd(name)
    pkt = G.build_packet(cmd, generate_long_request_id(), req_msg=G.request_from_dict(req_cls, data))
    headers = {'content-type': 'application/x-protobuf', 'accept': '*/*', 'origin': 'https://open-dev.feishu.cn', 'referer': 'https://open-dev.feishu.cn/', 'user-agent': UA,
               'x-appid': '161471', 'x-command': str(cmd), 'x-command-version': '5.7.0', 'x-lgw-os-type': '1', 'x-lgw-terminal-type': '2', 'x-source': 'web', 'x-web-version': '3.9.32', 'x-request-id': generate_long_request_id()}
    r = requests.post(builders.GATEWAY_URL, headers=headers, cookies=auth.cookies, data=pkt.SerializeToString(), timeout=20)
    out = P.Packet(); out.ParseFromString(r.content)
    envelope = {k: v for k, v in protobuf_to_dict(out).items() if k != 'payload'}
    body = G.response_to_dict(resp_cls, out.payload) if (resp_cls and out.payload and not envelope.get('status')) else {}
    return envelope, body, out.payload

def brief(chat):
    keys = ('id', 'name', 'type', 'ownerId', 'isCrossTenant', 'userCount', 'memberCount', 'chatMode', 'isPublic', 'postType', 'allowPost', 'chatable', 'isDissolved', 'lastMessagePosition')
    return {k: chat.get(k) for k in keys if k in chat}



from larkx.proto import decoders
me = str(auth.user_id)
group = sys.argv[1]
out = {'group': group, 'me': me, 'notes': []}

def desc(name):
    obj = L
    for part in name.split('.'): obj = getattr(obj, part)
    return [(f.name, f.type, f.label) for f in obj.DESCRIPTOR.fields]

def call(name, data):
    env, body, raw = api(name, data)
    if env.get('payloadType') not in (None, 1): out['notes'].append({'cmd': name, 'refused': env.get('sid')})
    return body

# people of the group
joined = {}
cursor = 0
while True:
    body = call('chats.PullChatChattersV2Request', {'chatId': int(group), 'limit': 100, **({'cursor': cursor} if cursor else {})})
    joined.update({str(k): int(v) for k, v in (body.get('chatterJoinTimeSec') or {}).items()})
    cursor = int(body.get('nextCursor') or 0)
    if not cursor or not body.get('chatterJoinTimeSec'): break
req = L.bots.PullChatBotsRequest(); req.chatId = group
pkt = P.Packet(); pkt.payloadType = 1; pkt.cmd = 13100; pkt.cid = uuid.uuid4().hex; pkt.payload = req.SerializeToString()
r = requests.post(builders.GATEWAY_URL, headers={'content-type': 'application/x-protobuf', 'accept': '*/*', 'origin': 'https://open-dev.feishu.cn', 'referer': 'https://open-dev.feishu.cn/', 'user-agent': UA, 'x-appid': '161471', 'x-command': '13100', 'x-command-version': '5.7.0', 'x-lgw-os-type': '1', 'x-lgw-terminal-type': '2', 'x-source': 'web', 'x-web-version': '3.9.32', 'x-request-id': uuid.uuid4().hex}, cookies=auth.cookies, data=pkt.SerializeToString(), timeout=20)
o = P.Packet(); o.ParseFromString(r.content); bots = L.bots.PullChatBotsResponse(); bots.ParseFromString(o.payload)
bot_ids = {str(b.get('id') or k) for c in protobuf_to_dict(bots).get('botCollection', []) for k, b in (c.get('bots') or {}).items()}
people = [u for u in joined if u != me and u not in bot_ids]

# names and aliases
chatters = (call('chatters.PullChattersByIdsRequest', {'chatterIds': people}).get('chatters') or {})
out['chatter_keys'] = sorted({k for c in chatters.values() for k in c})[:60]
members = {}
for uid in people:
    c = chatters.get(uid) or chatters.get(int(uid)) or {}
    members[uid] = {'name': c.get('name') or c.get('localizedName'), 'alias': c.get('alias'), 'another_name': c.get('anotherName'), 'description': (c.get('description') or {}).get('text') if isinstance(c.get('description'), dict) else c.get('description'),
                    'tenant_id': str(c.get('tenantId') or ''), 'joined_at': joined[uid]}

# request shape of the one-to-one lookup (the profile commands have no message classes in this library, so a profile memo cannot be read here)
try: out['p2p_request_fields'] = desc('chats.CheckP2PChatsExistByUserRequest')
except Exception as e: out['p2p_request_fields'] = 'ERR ' + type(e).__name__

# join and leave history of the group
history, cur = [], 0
for _ in range(40):
    body = call('chats.PullChatJoinLeaveHistoryRequest', {'chatId': int(group), 'count': 50, **({'cursor': cur} if cur else {})})
    rows = body.get('chatterHistory') or []
    history += [{'user_id': str(h.get('chatterId')), 'action': h.get('action'), 'event': h.get('event'), 'at': int(h.get('createTime') or 0), 'type': h.get('chatterType')} for h in rows]
    if not body.get('hasMore') or not rows: break
    cur = int(body.get('nextCursor') or 0)
out['history'] = history

# one-to-one chats
try:
    body = call('chats.CheckP2PChatsExistByUserRequest', {'chatterIds': people})
    out['p2p_check'] = {'keys': sorted(body.keys()), 'sample': mask({k: v for k, v in list(body.items())[:2]})}
    exist = body
except Exception as e:
    out['p2p_check'] = 'ERR ' + type(e).__name__ + ' ' + str(e)[:120]; exist = {}
p2p = {}
for key, value in exist.items():
    if isinstance(value, dict):
        for uid, chat_id in value.items():
            if str(uid) in members and chat_id: p2p[str(uid)] = str(chat_id if not isinstance(chat_id, dict) else chat_id.get('chatId') or chat_id.get('id') or '')
if not p2p:
    # fall back on the conversation list: one-to-one chats carry the other person's id
    for feed in ('INBOX', 'DONE'):
        cur = None
        for _ in range(40):
            data = {'type': feed, 'pullType': 'REFRESH' if cur is None else 'LOAD_MORE', 'count': 50}
            if cur is not None: data['cursor'] = int(cur)
            body = call('feed.PullFeedCardsRequest', data)
            chats = ((body.get('entity') or {}).get('chats') or body.get('chats') or {})
            for cid, chat in (chats.items() if isinstance(chats, dict) else []):
                if int(chat.get('type') or 0) == 1 and str(chat.get('chatterId') or '') in members: p2p[str(chat.get('chatterId'))] = str(chat.get('id') or cid)
            out.setdefault('feed_keys', sorted(body.keys())[:12])
            cur = body.get('nextCursor')
            if not body.get('hasMore') or cur is None: break
out['p2p_found'] = len(p2p)

for uid, chat_id in p2p.items():
    info = next(iter((call('chats.PullChatsByIdsRequest', {'chatIds': [chat_id]}).get('chats') or {}).values()), {})
    last = int(info.get('lastMessagePosition') or 0)
    first = max(1, last - 599)
    msgs = []
    for start in range(first, last + 1, 50):
        body = call('messages.PullMessagesByPositionsRequest', {'chatId': chat_id, 'positions': list(range(start, min(last, start + 49) + 1))})
        for m in (body.get('messages') or {}).values():
            mtype = decoders.enum_to_int(m.get('type', 0))
            try: summary, _ = decoders.decode_message_content(mtype, m.get('content', b''))
            except Exception: summary = ''
            msgs.append({'pos': int(m.get('position') or 0), 'at': int(m.get('createTime') or 0), 'mine': str(m.get('fromId')) == me, 'type': decoders.MSG_TYPE_NAMES.get(mtype, str(mtype)), 'text': str(summary)[:400]})
    members[uid]['p2p_chat_id'] = chat_id
    members[uid]['p2p_last_position'] = last
    members[uid]['messages'] = sorted(msgs, key=lambda x: x['pos'])
out['members'] = members
print(json.dumps(out, ensure_ascii=False, default=str))
