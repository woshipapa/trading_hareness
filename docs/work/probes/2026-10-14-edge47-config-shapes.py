import re, os, json, glob, sys
SECRET = re.compile(r"(token|secret|key|password|passwd|cookie|auth|sign|pwd|encrypt|credential)", re.I)
LOCAL = ("127.0.0.1","localhost","172.17.0.1","172.30.","47.114.113.152","100.98.179.101","118.178.133.37","47.110.79.189","host.docker.internal")
def shape(v):
    v=v.strip().strip('"').strip("'")
    if v=="": return "EMPTY"
    m=re.match(r"^([a-z][a-z0-9+.-]*)://(?:[^@/]*@)?([^/:?#]+)(?::(\d+))?",v,re.I)
    if m: return f"url {m.group(1)}://{m.group(2)}{':'+m.group(3) if m.group(3) else ''}"
    if "/" in v and re.match(r"^[~/]",v): return f"path {v[:60]}"
    if v.lower() in ("true","false","0","1","yes","no","on","off"): return f"flag {v}"
    return f"len={len(v)}"
def audit(path):
    print(f"--- {path} ({os.stat(path).st_size} B, mode {oct(os.stat(path).st_mode)[-3:]})")
    for ln in open(path, errors="replace"):
        ln=ln.rstrip("\n")
        if not ln.strip() or ln.lstrip().startswith("#") or "=" not in ln: continue
        k,_,v=ln.partition("=")
        k=k.strip().removeprefix("export ").strip()
        s=shape(v)
        tags=[]
        if SECRET.search(k) and s!="EMPTY": tags.append("secret")
        raw=v.strip().strip('"').strip("'")
        hit=[l for l in LOCAL if l in raw]
        if hit: tags.append("LOCAL-ADDR:"+",".join(hit))
        print(f"  {k:48s} {s}{'  ['+' '.join(tags)+']' if tags else ''}")
for p in ["/etc/feishu-relay-edge/runtime.env","/etc/larkagentx-group-relay.env","/etc/itougu-neican.env","/etc/itougu-perf-report.env","/etc/quant-intraday-edge/shared-gateway.env"]:
    if os.path.exists(p): audit(p)
print("--- json/other secret files (keys/shape only)")
for p in ["/opt/itougu-neican/itougu_auth.json","/etc/feishu-relay-edge/xhs-cookie"]:
    if os.path.isdir(p): print(p,"dir:",os.listdir(p)); continue
    if not os.path.exists(p): print(p,"MISSING"); continue
    raw=open(p,errors="replace").read()
    try:
        d=json.loads(raw); print(p, "json keys:", sorted(d)[:20] if isinstance(d,dict) else type(d).__name__)
    except Exception:
        print(p, f"text len={len(raw)}")
print("--- dirs under /etc/feishu-relay-edge:", [d for d in os.listdir("/etc/feishu-relay-edge") if os.path.isdir("/etc/feishu-relay-edge/"+d)])
print("--- certs:", sorted(os.listdir("/opt/feishu-relay-edge/certs")))
print("--- compose inline env keys (names, shape)")
txt=open("/opt/feishu-relay-edge/docker-compose.yml").read()
for m in re.finditer(r"^\s{6}([A-Z0-9_]+):\s*(.*)$",txt,re.M):
    k,v=m.group(1),m.group(2).strip()
    if v.startswith("${"): src="from-env "+re.sub(r"[:-].*","",v[2:])
    else: src=shape(v)
    if SECRET.search(k) or any(l in v for l in LOCAL): print(f"  {k:44s} {src}{' [secret-inline]' if SECRET.search(k) and not v.startswith('${') and v else ''}")
