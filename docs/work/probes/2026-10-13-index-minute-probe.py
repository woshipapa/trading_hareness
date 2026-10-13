import json, re, urllib.request
env = open("/Users/papa/codebase/n8n/deploy/shared-peer/.env").read()
KEY = re.search(r"^QUANT_SHARED_READ_API_KEY=(.*)$", env, re.M).group(1).strip().strip('"')
def call(action, code, day):
    body = json.dumps({"target": "longhu_history", "params": {"PhoneOSNew": "1", "VerSion": "5.20.0.2", "c": "StockL2History", "apiv": "w41",
                                                              "a": action, "StockID": code, "Day": day}}).encode()
    request = urllib.request.Request("http://127.0.0.1:15682/licensed/stock-api/call", data=body, headers={"X-Quant-Read-Key": KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["pages"][0]["payload"]
for code, day in (("510300", "20261009"), ("512100", "20230904"), ("159915", "20240304"), ("SH000001", "20261009"), ("999999", "20261009"), ("563300", "20261009")):
    try:
        body = call("GetStockTrend", code, day)
        trend = body.get("trend") or []
        other = {k: (v if not isinstance(v, (list, dict)) else f"{type(v).__name__}[{len(v)}]") for k, v in body.items() if k != "trend"}
        print(code, day, "points", len(trend), "first", trend[0] if trend else None, "| 11:18", next((x for x in trend if x[0] == "11:18"), None), "| other keys", dict(list(other.items())[:10]))
    except Exception as error:
        print(code, day, "error", type(error).__name__, str(error)[:100])
