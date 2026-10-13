"""W010 step 0, stage C: add and remove one member in the self-test group; send one text to the user and mark it urgent in the app.
Run by the user (docs/work/probes/2026-10-13-relay-auto-p0-c.sh): it notifies the user's own Feishu account.
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


import time
me = str(auth.user_id)
selftest, pair_chat = sys.argv[1], sys.argv[2]

def members(chat_id):
    env, body, _ = api('chats.PullChatChattersV2Request', {'chatId': int(chat_id), 'limit': 50})
    return [str(k) for k in (body.get('chatterJoinTimeSec') or {}).keys()], env

def failure(env, raw):
    return raw[:200].decode('utf-8', 'replace') if env.get('payloadType') != 1 else ''

ids, env = members(pair_chat)
others = [i for i in ids if i != me]
print('pair chat members', len(ids), '| others', len(others), '| envelope', mask(env))
other = others[0]

env, body, raw = api('chats.PutChatChatterRequest', {'chatId': selftest, 'chatterIds': [other]})
print('P0-7 PutChatChatter envelope', mask(env), '| keys', sorted(body.keys()), '| chat', brief(body.get('chat') or {}), '| payload text', failure(env, raw))
time.sleep(2)
now, _ = members(selftest)
print('  self-test members now', len(now), '| invited member present', other in now)
env, body, raw = api('chats.DeleteChatChatterRequest', {'chatId': selftest, 'chatterIds': [other]})
print('  DeleteChatChatter envelope', mask(env), '| keys', sorted(body.keys()), '| chat', brief(body.get('chat') or {}), '| payload text', failure(env, raw))
time.sleep(2)
now, _ = members(selftest)
print('  self-test members after removal', len(now), '| member still present', other in now)

pkt = builders.build_send_message_packet('【中继加急试验】这是一条接口试验消息，随后会被加急一次，请忽略。', pair_chat, generate_long_request_id())
headers = {'content-type': 'application/x-protobuf', 'accept': '*/*', 'origin': 'https://open-dev.feishu.cn', 'referer': 'https://open-dev.feishu.cn/', 'user-agent': UA,
           'x-appid': '161471', 'x-command': str(pkt.cmd), 'x-command-version': '5.7.0', 'x-lgw-os-type': '1', 'x-lgw-terminal-type': '2', 'x-source': 'web', 'x-web-version': '3.9.32', 'x-request-id': generate_long_request_id()}
r = requests.post(builders.GATEWAY_URL, headers=headers, cookies=auth.cookies, data=pkt.SerializeToString(), timeout=20)
out = P.Packet(); out.ParseFromString(r.content)
env = {k: v for k, v in protobuf_to_dict(out).items() if k != 'payload'}
print('P0-6 send text: cmd', pkt.cmd, 'envelope', mask(env), '| response payload bytes', len(out.payload))
time.sleep(1)
_, body, _ = api('chats.PullChatsByIdsRequest', {'chatIds': [pair_chat]})
last = int(next(iter((body.get('chats') or {}).values()), {}).get('lastMessagePosition') or 0)
_, body, _ = api('messages.PullMessagesByPositionsRequest', {'chatId': pair_chat, 'positions': [last - 1, last]})
mine = sorted((m for m in (body.get('messages') or {}).values() if str(m.get('fromId')) == me), key=lambda m: int(m.get('position') or 0))
sent_id = str(mine[-1].get('id')) if mine else ''
print('  sent message found', bool(sent_id))
env, body, raw = api('urgent.PutUrgentRequest', {'messageId': sent_id, 'chatterIds': [other], 'urgentType': 'APP', 'chatId': pair_chat})
print('  PutUrgent APP envelope', mask(env), '| body', mask(body), '| payload text', failure(env, raw))
time.sleep(2)
env, body, raw = api('urgent.PullUrgentsAckStatusRequest', {'messageIds': [sent_id]})
print('  PullUrgentsAckStatus envelope', mask(env), '| body', json.dumps(mask(body), ensure_ascii=False)[:500])
