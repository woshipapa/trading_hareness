"""W010 step 0, Read-only: the custom bots of the given groups (name, keywords, who made them) and whether a bot's address is one the itougu listener posts to.
No address is printed: addresses are compared by hash in memory.
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




import hashlib
me = str(auth.user_id)
digest = lambda text: hashlib.sha256(str(text).encode('utf-8')).hexdigest()

def env_map(path, key):
    """`chat_id=value;chat_id2=value2` of one setting in an env file, read in memory."""
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if line.startswith(key + '='):
            value = line[len(key) + 1:].strip().strip('"').strip("'")
            return dict(part.split('=', 1) for part in value.split(';') if '=' in part)
    return {}

listener = {digest(url.strip()): chat_id.strip() for chat_id, url in env_map('/etc/itougu-neican.env', 'ITOUGU_FEISHU_WEBHOOKS').items()}
keywords = {chat_id.strip(): word.strip() for chat_id, word in env_map('/etc/itougu-neican.env', 'ITOUGU_FEISHU_WEBHOOK_KEYWORDS').items()}

def chat_bots(chat_id):
    req = L.bots.PullChatBotsRequest(); req.chatId = str(chat_id)
    pkt = P.Packet(); pkt.payloadType = 1; pkt.cmd = 13100; pkt.cid = uuid.uuid4().hex; pkt.payload = req.SerializeToString()
    r = requests.post(builders.GATEWAY_URL, headers={'content-type': 'application/x-protobuf', 'accept': '*/*', 'origin': 'https://open-dev.feishu.cn', 'referer': 'https://open-dev.feishu.cn/', 'user-agent': UA, 'x-appid': '161471', 'x-command': '13100', 'x-command-version': '5.7.0', 'x-lgw-os-type': '1', 'x-lgw-terminal-type': '2', 'x-source': 'web', 'x-web-version': '3.9.32', 'x-request-id': uuid.uuid4().hex}, cookies=auth.cookies, data=pkt.SerializeToString(), timeout=20)
    o = P.Packet(); o.ParseFromString(r.content); out = L.bots.PullChatBotsResponse(); out.ParseFromString(o.payload)
    return protobuf_to_dict(out)

def chatbot(name, body):
    headers = {'content-type': 'text/plain;charset=UTF-8', 'accept': '*/*', 'origin': 'https://icn39lg5vsjh.feishu.cn', 'referer': 'https://icn39lg5vsjh.feishu.cn/', 'user-agent': UA}
    r = requests.post(f'https://open.feishu.cn/open-apis/chatbot/api/{name}', headers=headers, cookies=auth.cookies, data=json.dumps(body), timeout=20)
    try: return r.json()
    except ValueError: return {'code': -1, 'msg': f'HTTP {r.status_code}'}

result = {'listener_chat_ids': sorted(listener.values()), 'listener_keywords': keywords, 'groups': {}}
for group in sys.argv[1:]:
    rows = []
    for collection in chat_bots(group).get('botCollection') or []:
        for key, bot in (collection.get('bots') or {}).items():
            row = {'collection_type': collection.get('type'), 'bot_id': str(bot.get('id') or key), 'name': bot.get('name'), 'made_by_me': str(bot.get('creatorId') or '') == me}
            info = chatbot('GetWebhookBotInfo', {'bot_id': row['bot_id'], 'i18n': 'zh-CN'})
            data = info.get('data') or {}
            row.update({'info_code': info.get('code'), 'key_words': data.get('key_words'), 'signature_on': data.get('signature_status'), 'ip_limited': bool(data.get('allow_ips')),
                        'address_readable': bool(data.get('webhook')), 'listener_chat_id': listener.get(digest(str(data.get('webhook') or '').strip())) if data.get('webhook') else None})
            rows.append(row)
    result['groups'][group] = rows
print(json.dumps(result, ensure_ascii=False))
