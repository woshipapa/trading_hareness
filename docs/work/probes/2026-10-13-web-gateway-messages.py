"""Read-only: print the field lists of the gateway messages the plan needs, from the library's generated classes."""
import json, sys
sys.path.insert(0, '/opt/larkagentx/source')
from larkx.proto import lark_all_pb2 as L
cmd_map = json.load(open('/opt/larkagentx/source/larkx/proto/cmd_map.json'))['by_req']
TYPES = {1: 'double', 2: 'float', 3: 'int64', 4: 'uint64', 5: 'int32', 8: 'bool', 9: 'string', 11: 'message', 12: 'bytes', 13: 'uint32', 14: 'enum', 17: 'sint32', 18: 'sint64'}
def resolve(name):
    obj = L
    for part in name.split('.'):
        obj = getattr(obj, part)
    return obj
def fields(desc, depth=0, seen=()):
    out = []
    for f in desc.fields:
        label = 'repeated ' if f.label == 3 else ''
        kind = TYPES.get(f.type, str(f.type))
        if f.type == 11:
            kind = f.message_type.name
            if f.message_type.GetOptions().map_entry:
                v = f.message_type.fields_by_name['value']
                kind = 'map<%s,%s>' % (TYPES.get(f.message_type.fields_by_name['key'].type), v.message_type.name if v.type == 11 else TYPES.get(v.type)); label = ''
        if f.type == 14:
            kind = 'enum %s{%s}' % (f.enum_type.name, ','.join('%s=%d' % (v.name, v.number) for v in f.enum_type.values[:12]))
        out.append('%s%s%s %s' % ('  ' * depth, label, kind, f.name))
        if f.type == 11 and depth < 1 and f.message_type.name not in seen and not f.message_type.GetOptions().map_entry and len(f.message_type.fields) <= 14:
            out += fields(f.message_type, depth + 1, seen + (f.message_type.name,))
    return out
for name in sys.argv[1:]:
    entry = cmd_map.get(name)
    if not entry:
        print('## %s  — not in the command table' % name); continue
    print('## %s  cmd %s -> %s' % (name, entry['cmd'], entry.get('resp')))
    try:
        print('\n'.join('  ' + line for line in fields(resolve(name).DESCRIPTOR)) or '  (no fields)')
        if entry.get('resp'):
            resp = fields(resolve(entry['resp']).DESCRIPTOR)
            print('  -> response:'); print('\n'.join('    ' + line for line in resp[:26]) or '    (no fields)')
            if len(resp) > 26: print('    … %d more lines' % (len(resp) - 26))
    except Exception as e:
        print('  (descriptor not available: %s)' % type(e).__name__)
