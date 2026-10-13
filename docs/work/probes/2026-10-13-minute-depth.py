import json, re, urllib.request
env = open("/Users/papa/codebase/n8n/deploy/shared-peer/.env").read()
KEY = re.search(r"^QUANT_SHARED_READ_API_KEY=(.*)$", env, re.M).group(1).strip().strip('"')
def call(action, code, day):
    body = json.dumps({"target": "longhu_history", "params": {"PhoneOSNew": "1", "VerSion": "5.20.0.2", "c": "StockL2History", "apiv": "w41",
                                                              "a": action, "StockID": code, "Day": day}}).encode()
    request = urllib.request.Request("http://127.0.0.1:15682/licensed/stock-api/call", data=body, headers={"X-Quant-Read-Key": KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())
for code, day in (("000001", "20220905"), ("000001", "20230904"), ("600519", "20231120"), ("000001", "20240304"), ("002594", "20250102"), ("000001", "20260918")):
    try:
        data = call("GetStockTrend", code, day)
        body = data["pages"][0]["payload"]
        trend = body.get("trend") if isinstance(body, dict) else None
        keys = list(body.keys())[:8] if isinstance(body, dict) else type(body).__name__
        print(code, day, "points", len(trend) if trend else 0, "first", (trend[0][:3] if trend else None), "last", (trend[-1][:3] if trend else None), "| keys", keys if not trend else "")
    except Exception as error:
        print(code, day, "error", type(error).__name__, str(error)[:100])
