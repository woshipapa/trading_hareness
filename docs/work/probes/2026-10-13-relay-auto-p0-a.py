"""W010 step 0, stage A: create the self-test group, restrict posting, invite link, link info, join again, join applications.
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

me = str(auth.user_id)
chat_id = sys.argv[1] if len(sys.argv) > 1 else ''
if not chat_id:
    env, body, raw = api('chats.PutChatRequest', {'type': 'GROUP', 'groupName': '中继自测群', 'groupDesc': '转发中继的接口试验场，可忽略', 'isCrossTenant': True, 'cid': uuid.uuid4().hex})
    chat = body.get('chat') or {}
    print('P0-1 PutChat envelope', mask(env), '| response keys', sorted(body.keys()), '| chat', brief(chat), '| owner is me', str(chat.get('ownerId')) == me)
    if env.get('status') or not chat.get('id'):
        print('  raw payload (first 200 bytes as text):', raw[:200].decode('utf-8', 'replace')); raise SystemExit('create failed; stopping')
    chat_id = str(chat['id'])
print('chat_id', chat_id)

env, body, raw = api('chats.PatchChatPostUserRequest', {'chatId': chat_id, 'postType': 'ONLY_ADMIN'})
print('P0-2 PatchChatPostUser ONLY_ADMIN envelope', mask(env), 'body', body, '| payload text', raw[:160].decode('utf-8', 'replace') if env.get('status') else '')
env, body, _ = api('chats.PullChatsByIdsRequest', {'chatIds': [chat_id]})
chat = next(iter((body.get('chats') or {}).values()), {})
print('  chat now', brief(chat), '| keys with post/permission:', sorted(k for k in chat if re.search('(?i)post|permission|setting', k))[:14])
for k in sorted(k for k in chat if re.search('(?i)post', k)): print('   ', k, '=', mask(chat[k]))

env, body, raw = api('chats.PullChatLinkTokenRequest', {'chatId': chat_id, 'expiredDay': 7})
token = body.get('token') or ''
url = urlsplit(body.get('sharedUrl') or '')
print('P0-3 PullChatLinkToken envelope', mask(env), '| token length', len(token), '| url', f'{url.scheme}://{url.netloc}{url.path}', '| query keys', [k for k, _ in parse_qsl(url.query)],
      '| token is a query value:', any(v == token for _, v in parse_qsl(url.query)), '| kouLing length', len(body.get('kouLing') or ''), '| expireTime', body.get('expireTime'), '| isUnlimited', body.get('isUnlimited'))
if token:
    env, body, raw = api('chats.PullChatLinkInfoRequest', {'token': token})
    print('  PullChatLinkInfo envelope', mask(env), '| keys', sorted(body.keys()), '| alreadyInChat', body.get('alreadyInChat'), '| chat', brief(body.get('chat') or {}),
          '| owner keys', sorted((body.get('ownerChatter') or {}).keys())[:8], '| owner name', (body.get('ownerChatter') or {}).get('name'), '| expireTime', body.get('expireTime'))
    env, body, raw = api('chats.PutChatterViaLinkRequest', {'token': token})
    print('  PutChatterViaLink (already a member) envelope', mask(env), '| keys', sorted(body.keys()), '| chat', brief(body.get('chat') or {}), '| payload text', raw[:200].decode('utf-8', 'replace') if env.get('status') else '')

for target in sys.argv[2:]:
    env, body, raw = api('chats.PullChatApplicationsRequest', {'type': 'JOIN_CHAT', 'count': 20, 'isDescOrder': True, 'chatId': target})
    apps = body.get('applications') or []
    print('P0-4 PullChatApplications', target, 'envelope', mask(env), '| keys', sorted(body.keys()), '| rows', len(apps), '| statuses', sorted({str(a.get('status')) for a in apps}),
          '| row keys', sorted(apps[0].keys()) if apps else None, '| contactSummary keys', sorted((apps[0].get('contactSummary') or {}).keys())[:10] if apps else None)
