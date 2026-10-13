"""W010 step 0, P0-9: can a custom bot's webhook @-mention a member by the id the member list gives? Only the self-test group and the relay account itself are involved.
The webhook address is read in memory and never printed.
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
from larkx.proto import decoders
me = str(auth.user_id)
chat_id = sys.argv[1]

def chatbot(name, body):
    r = requests.post(f'https://open.feishu.cn/open-apis/chatbot/api/{name}', headers={'content-type': 'application/json', 'origin': 'https://open-dev.feishu.cn', 'referer': 'https://open-dev.feishu.cn/', 'user-agent': UA}, cookies=auth.cookies, data=json.dumps(body), timeout=20)
    return r.json()

req = L.bots.PullChatBotsRequest(); req.chatId = chat_id
pkt = P.Packet(); pkt.payloadType = 1; pkt.cmd = 13100; pkt.cid = uuid.uuid4().hex; pkt.payload = req.SerializeToString()
r = requests.post(builders.GATEWAY_URL, headers={'content-type': 'application/x-protobuf', 'accept': '*/*', 'origin': 'https://open-dev.feishu.cn', 'referer': 'https://open-dev.feishu.cn/', 'user-agent': UA, 'x-appid': '161471', 'x-command': '13100', 'x-command-version': '5.7.0', 'x-lgw-os-type': '1', 'x-lgw-terminal-type': '2', 'x-source': 'web', 'x-web-version': '3.9.32', 'x-request-id': uuid.uuid4().hex}, cookies=auth.cookies, data=pkt.SerializeToString(), timeout=20)
out = P.Packet(); out.ParseFromString(r.content); bots = L.bots.PullChatBotsResponse(); bots.ParseFromString(out.payload)
bot_id = next(str(b.get('id') or k) for c in protobuf_to_dict(bots).get('botCollection', []) if c.get('type') == 1 for k, b in (c.get('bots') or {}).items() if b.get('name') == '转发机器人')
info = chatbot('GetWebhookBotInfo', {'bot_id': bot_id, 'i18n': 'zh-CN'})['data']
hook, word = info['webhook'], info['key_words'][0]
print('bot found', bool(hook), '| keyword', word)

trials = [
    ('text, numeric id', {'msg_type': 'text', 'content': {'text': f'{word} 到期提醒试验A <at user_id="{me}">会员</at> 请忽略'}}),
    ('post, numeric id', {'msg_type': 'post', 'content': {'post': {'zh_cn': {'title': '', 'content': [[{'tag': 'text', 'text': f'{word} 到期提醒试验B '}, {'tag': 'at', 'user_id': me}, {'tag': 'text', 'text': ' 请忽略'}]]}}}}),
]
for label, body in trials:
    resp = requests.post(hook, json=body, timeout=15)
    try: answer = resp.json()
    except Exception: answer = {}
    print('send', label, '-> http', resp.status_code, 'code', answer.get('code', answer.get('StatusCode')), 'msg', answer.get('msg', answer.get('StatusMessage')))
time.sleep(2)
env, body, _ = api('chats.PullChatsByIdsRequest', {'chatIds': [chat_id]})
last = int(next(iter((body.get('chats') or {}).values()), {}).get('lastMessagePosition') or 0)
env, body, _ = api('messages.PullMessagesByPositionsRequest', {'chatId': chat_id, 'positions': [last - 1, last]})
for m in sorted((body.get('messages') or {}).values(), key=lambda m: int(m.get('position') or 0)):
    mtype = decoders.enum_to_int(m.get('type', 0))
    summary, data = decoders.decode_message_content(mtype, m.get('content', b''))
    text = json.dumps(data, ensure_ascii=False, default=str)
    ats = re.findall(r'"(?:userId|user_id|atUserId)"\s*:\s*"?(\d+|ou_\w+|all)', text)
    print(' pos', m.get('position'), decoders.MSG_TYPE_NAMES.get(mtype, mtype), 'from bot', str(m.get('fromId')) == bot_id, '| summary', str(summary).replace('\n', ' ')[:80],
          '| at ids found', ats, '| mentions me', me in ats, '| keys', sorted(k for k in m if re.search('(?i)at|mention', k))[:8], '|', mask({k: m[k] for k in m if re.search('(?i)^isAt|atUser|mention', k)}))
    if not ats: print('   data', text[:300])
