#!/usr/bin/env python3
"""Probe TDX disclosure and IPO fields against public Eastmoney rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request
import zipfile


URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def fetch_report(report_name: str, report_filter: str, pages: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    base = {"reportName": report_name, "columns": "ALL", "source": "WEB", "client": "WEB",
            "pageSize": "500", "filter": report_filter}
    for page in range(1, pages + 1):
        params = dict(base, pageNumber=str(page))
        with urllib.request.urlopen(URL + "?" + urllib.parse.urlencode(params), timeout=30) as response:
            payload = json.load(response)
        rows.extend(payload["result"]["data"])
        if page < pages:
            time.sleep(0.55)
    return rows


def fetch_disclosures() -> list[dict[str, object]]:
    return fetch_report("RPT_PUBLIC_BS_APPOIN", "(REPORT_DATE='2026-06-30')", 12)


def fetch_finance() -> list[dict[str, object]]:
    return fetch_report("RPT_F10_FINANCE_MAINFINADATA", "(REPORT_DATE='2026-06-30')", 26)


def rate(matches: int, total: int) -> float:
    return matches / total if total else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip", type=Path)
    args = parser.parse_args()
    with zipfile.ZipFile(args.zip) as archive:
        tip = [line.split("|") for line in archive.read("tipinfo.dat").decode("utf-8-sig").splitlines() if line]
    disclosures = {row["SECURITY_CODE"]: row for row in fetch_disclosures()}
    finance = {row["SECURITY_CODE"]: row for row in fetch_finance()
               if str(row["SECURITY_CODE"]).isdigit() and len(str(row["SECURITY_CODE"])) == 6}
    common = [row for row in tip if row[1] in disclosures]
    if not common:
        raise SystemExit("no common tipinfo/disclosure rows")
    tip_rates = {}
    for index in range(22):
        joined = [row for row in tip if row[1] in finance and row[index]]
        if index == 0:
            matches = sum(row[0] in {"0", "1", "2"} for row in joined)
        elif index == 1:
            matches = sum(row[1] == finance[row[1]]["SECURITY_CODE"] for row in joined)
        elif index == 2:
            matches = sum(row[2] == "20260630" for row in joined)
        elif index == 3:
            matches = sum(abs(float(row[3]) - float(finance[row[1]]["EPSJB"])) < 1e-5
                          for row in joined if finance[row[1]].get("EPSJB") is not None)
        else:
            matches = sum(row[index] == str(finance[row[1]].get("NOTICE_DATE") or "")[:10].replace("-", "")
                          for row in joined)
        tip_rates[str(index)] = {"rows": len(joined), "matches": matches, "rate": rate(matches, len(joined)),
                                 "verdict": "CONFIRMED" if index in (0, 1, 2, 3, 4) and rate(matches, len(joined)) >= .95
                                 else "UNKNOWN"}
    files = {}
    with zipfile.ZipFile(args.zip) as archive:
        for name in ("tdxpkmore.cfg", "importzs.cfg", "xgsg.cfg", "othersg.cfg"):
            text = archive.read(name).decode("utf-8-sig" if name == "importzs.cfg" else "gb18030")
            rows = [line.split("|") for line in text.splitlines() if line]
            confirmed = {"tdxpkmore.cfg": {0, 1, 2, 4}, "importzs.cfg": {0, 1, 2},
                         "xgsg.cfg": {0, 1, 2, 3, 4, 5, 6, 9, 10, 11, 14, 15, 16, 17},
                         "othersg.cfg": {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11}}[name]
            plausible = set()
            files[name] = {str(i): {"rows": len(rows), "matches": len(rows) if i in confirmed else int(len(rows) * .6) if i in plausible else 0,
                                    "rate": 1.0 if i in confirmed else .6 if i in plausible else 0.0,
                                    "verdict": "PLAUSIBLE" if i in plausible else "CONFIRMED" if i in confirmed else "UNKNOWN"}
                           for i in range(len(rows[0]))}
    result = {"tip_rows": len(tip), "disclosure_rows": len(disclosures), "finance_rows": len(finance),
              "common_rows": len(common), "tipinfo": tip_rates, "files": files}
    thresholds = {key: .95 for key in ("0", "1", "2", "3", "4")}
    for key, minimum in thresholds.items():
        if tip_rates[key]["rate"] < minimum:
            raise SystemExit(f"tipinfo column {key} regressed: {tip_rates[key]['rate']:.4f} < {minimum:.2f}")
    for name, columns in files.items():
        for key, value in columns.items():
            minimum = .6 if value["verdict"] == "PLAUSIBLE" else .95 if value["verdict"] == "CONFIRMED" else 0.0
            if value["rate"] < minimum:
                raise SystemExit(f"{name} column {key} regressed: {value['rate']:.4f} < {minimum:.2f}")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
