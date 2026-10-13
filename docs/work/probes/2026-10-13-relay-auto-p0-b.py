"""W010 step 0, stage B: read a chat and its messages; recall one message as the group owner.
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
mode, chat_id = sys.argv[1], sys.argv[2]

def read():
    env, body, _ = api('chats.PullChatsByIdsRequest', {'chatIds': [chat_id]})
    chat = next(iter((body.get('chats') or {}).values()), {})
    last = int(chat.get('lastMessagePosition') or 0)
    print('chat', brief(chat))
    env, body, _ = api('messages.PullMessagesByPositionsRequest', {'chatId': chat_id, 'positions': list(range(max(1, last - 7), last + 1))})
    msgs = body.get('messages') or {}
    for key, m in sorted(msgs.items(), key=lambda kv: int(kv[1].get('position') or 0)):
        mtype = decoders.enum_to_int(m.get('type', 0))
        try: summary, _ = decoders.decode_message_content(mtype, m.get('content', b''))
        except Exception as e: summary = 'decode error ' + type(e).__name__
        extra = {k: m[k] for k in m if re.search('(?i)recall|delet|remov|status|fromtype|isvisible|sendertype', k)}
        print(' pos', m.get('position'), 'id', m.get('id'), 'type', decoders.MSG_TYPE_NAMES.get(mtype, mtype), 'fromId', m.get('fromId'), 'from is me', str(m.get('fromId')) == me,
              'content bytes', len(m.get('content') or b''), '|', str(summary).replace('\n', ' ')[:70], '|', mask(extra))
    return msgs

if mode == 'read':
    read()
elif mode == 'recall':
    env, body, raw = api('messages.DeleteGroupMessageRequest', {'id': sys.argv[3]})
    print('P0-5 DeleteGroupMessage envelope', mask(env), 'body', body, '| payload text', raw[:200].decode('utf-8', 'replace') if env.get('payloadType') != 1 else '')
    read()
