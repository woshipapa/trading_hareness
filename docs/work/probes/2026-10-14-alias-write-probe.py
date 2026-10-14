"""W010 step 0, WRITES, then restores: the alias (备注名) the account gives to one contact. It is written three times: the same value, the value plus one
character, and the original again, with a read after each. Nobody but the account can see an alias; nothing is sent to anyone.
Shows what the gateway answers to `users.PutUserAliasRequest` (5400) and whether the alias reads back.
arguments: <group id> <the contact's current alias, exactly>
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




import time
group, wanted = sys.argv[1], sys.argv[2]

def aliases(ids):
    env, body, raw = api('chatters.PullChattersByIdsRequest', {'chatterIds': ids})
    return {str(uid): (chatter.get('alias') or '') for uid, chatter in (body.get('chatters') or {}).items()}

env, body, raw = api('chats.PullChatChattersV2Request', {'chatId': int(group), 'limit': 100})
members = [str(uid) for uid in (body.get('chatterJoinTimeSec') or {})]
matches = [uid for uid, alias in aliases(members).items() if alias == wanted]
if len(matches) != 1:
    print(json.dumps({'status': 'not run', 'reason': f'{len(matches)} members of the group have exactly this alias; one is needed'}, ensure_ascii=False)); sys.exit(1)
uid = matches[0]

def read():
    return aliases([uid]).get(uid, '')

def put(name):
    try:
        env, body, raw = api('users.PutUserAliasRequest', {'targetId': int(uid), 'name': name})
        return {'payloadType': env.get('payloadType'), 'sid': str(env.get('sid') or '')[:160], 'body': body}
    except Exception as error:
        return {'error': type(error).__name__ + ' ' + str(error)[:200]}

out = {'status': 'run', 'before': read()}
out['write_same_value'] = put(out['before']); time.sleep(1); out['read_after_same_value'] = read()
out['write_one_more_character'] = put(out['before'] + '·'); time.sleep(1); out['read_after_change'] = read()
out['write_original_again'] = put(out['before']); time.sleep(1); out['read_after_restore'] = read()
out['changed_as_written'] = out['read_after_change'] == out['before'] + '·'
out['restored'] = out['read_after_restore'] == out['before']
print(json.dumps(out, ensure_ascii=False, default=str))
