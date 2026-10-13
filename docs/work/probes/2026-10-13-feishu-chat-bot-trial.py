"""One trial with the bridge's own session: create a custom bot in a chat, set its keyword, send one test message, delete it.
The webhook address and the signing secret are never printed."""
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

def gateway(cmd, req, resp_cls):
    pkt = P.Packet(); pkt.payloadType = 1; pkt.cmd = cmd; pkt.cid = uuid.uuid4().hex; pkt.payload = req.SerializeToString()
    headers = {'content-type': 'application/x-protobuf', 'accept': '*/*', 'origin': 'https://open-dev.feishu.cn', 'referer': 'https://open-dev.feishu.cn/',
               'user-agent': UA, 'x-appid': '161471', 'x-command': str(cmd), 'x-command-version': '5.7.0', 'x-lgw-os-type': '1', 'x-lgw-terminal-type': '2',
               'x-source': 'web', 'x-web-version': '3.9.32', 'x-request-id': uuid.uuid4().hex}
    r = requests.post(builders.GATEWAY_URL, headers=headers, cookies=auth.cookies, data=pkt.SerializeToString(), timeout=15)
    out = P.Packet(); out.ParseFromString(r.content)
    print('  http', r.status_code, 'packet status', getattr(out, 'status', None), 'payload bytes', len(out.payload))
    msg = resp_cls(); msg.ParseFromString(out.payload)
    return protobuf_to_dict(msg)


SECRET_KEYS = {'webhook', 'signature'}
def public(data):
    return {k: ('<hidden, %d chars>' % len(str(v)) if k in SECRET_KEYS and v else v) for k, v in (data or {}).items()}

def chatbot(name, body):
    headers = {'content-type': 'text/plain;charset=UTF-8', 'accept': '*/*', 'origin': 'https://icn39lg5vsjh.feishu.cn', 'referer': 'https://icn39lg5vsjh.feishu.cn/', 'user-agent': UA}
    r = requests.post(f'https://open.feishu.cn/open-apis/chatbot/api/{name}', headers=headers, cookies=auth.cookies, data=json.dumps(body), timeout=20)
    try: out = r.json()
    except Exception: out = {'code': -1, 'msg': 'non-json http %s' % r.status_code}
    print(f'{name}: http {r.status_code} code {out.get("code")} msg {out.get("msg")!r}')
    return out

def chat_bots(chat_id):
    req = L.bots.PullChatBotsRequest(); req.chatId = chat_id
    data = gateway(13100, req, L.bots.PullChatBotsResponse)
    return {bid: (b.get('name'), c.get('type')) for c in data.get('botCollection', []) for bid, b in (c.get('bots') or {}).items()}

chat_id, keyword = sys.argv[1], sys.argv[2]
print('user id present', bool(auth.user_id))
before = chat_bots(chat_id); print('bots before', before)
req = L.bots.PullChatCandidateBotsRequest(); req.chatId = chat_id
template = next(iter((gateway(13101, req, L.bots.PullChatCandidateBotsResponse).get('functionalBots') or {}).values()))
print('template', template.get('id'), template.get('name'))

bot_id = None
try:
    created = chatbot('CreateWebhookBot', {'name': '转发机器人·接口试验', 'avatar_key': template['avatar']['key'], 'avatar_type': 0,
        'description': '自动配置的接口试验，随后删除', 'bot_type_id': template['id'], 'chat_id': chat_id, 'user_id': auth.user_id, 'i18n': 'zh-CN'})
    if created.get('code') != 0: raise SystemExit('create refused: ' + json.dumps(mask(created), ensure_ascii=False)[:400])
    bot_id = str((created.get('data') or {}).get('bot_id') or '')
    print('created bot_id', bot_id, 'data keys', sorted((created.get('data') or {}).keys()))

    info = chatbot('GetWebhookBotInfo', {'bot_id': bot_id, 'i18n': 'zh-CN'})
    data = info.get('data') or {}
    print('after create:', json.dumps(public({k: data.get(k) for k in ('name', 'enable', 'key_words', 'allow_ips', 'signature_status', 'check_mender', 'avatar_type', 'bot_type', 'type_name', 'webhook')}), ensure_ascii=False))

    updated = chatbot('UpdateWebhookBotInfo', {'avatar_key': data.get('avatar_key'), 'avatar_type': data.get('avatar_type', 0), 'name': data.get('name'), 'description': data.get('description'),
        'enable': True, 'check_mender': bool(data.get('check_mender')), 'signature_status': False, 'allow_ips': [], 'key_words': [keyword], 'bot_id': bot_id, 'i18n': 'zh-CN'})
    info = chatbot('GetWebhookBotInfo', {'bot_id': bot_id, 'i18n': 'zh-CN'})
    data = info.get('data') or {}
    print('after update:', json.dumps(public({k: data.get(k) for k in ('name', 'enable', 'key_words', 'allow_ips', 'signature_status', 'webhook')}), ensure_ascii=False))
    hook = str(data.get('webhook') or '')
    print('webhook looks like a custom-bot address:', bool(re.fullmatch(r'https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9-]{8,80}', hook)))

    for label, text in (('without keyword', '接口试验：这条不带关键词，应当被机器人拒绝'), ('with keyword', f'{keyword} 接口试验：自动创建的机器人发出的测试消息，机器人随后删除')):
        r = requests.post(hook, json={'msg_type': 'text', 'content': {'text': text}}, timeout=15)
        try: body = r.json()
        except Exception: body = {}
        print(f'send {label}: http {r.status_code} code {body.get("code", body.get("StatusCode"))} msg {body.get("msg", body.get("StatusMessage"))!r}')
finally:
    if bot_id:
        deleted = chatbot('DeleteBotResource', {'chat_id': chat_id, 'bot_id': bot_id, 'i18n': 'zh-CN'})
        after = chat_bots(chat_id)
        print('bots after', after, '| trial bot still listed:', bot_id in after)
