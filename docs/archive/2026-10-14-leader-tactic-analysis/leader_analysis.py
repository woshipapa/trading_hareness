import json, math, sys
from collections import defaultdict
from statistics import mean, pstdev

SRC = "state/research/minutes_20261014/leader_extract2.jsonl"
days, family = [], None
for line in open(SRC):
    d = json.loads(line)
    if "family" in d: family = d["family"]
    elif "day" in d: days.append(d)
print("family:", family)
print("sessions:", len(days), "rows/day median:", sorted(len(d["rows"]) for d in days)[len(days) // 2])

def tstat(values):
    values = [v for v in values if v is not None]
    n = len(values)
    if n < 3: return None, n
    m = mean(values); sd = (sum((v - m) ** 2 for v in values) / (n - 1)) ** 0.5
    return (m / (sd / math.sqrt(n)) if sd else None), n

def fmt(x, pct=True, d=2):
    if x is None: return "  --  "
    return f"{x*100:+.{d}f}%" if pct else f"{x:.{d}f}"

# ---- per tactic, per day: picks, theme means, comparison ----
METRIC = "held_close"
print("\n== #1 有效样本：题材日 vs 股票（held:close，选股均值 − 当日其余均值；日均值 t / 题材日均值按日分簇 t） ==")
print(f"{'战法':<30}{'选股日':>6}{'选股':>6}{'题材日':>7}{'每题材日只数':>10}  {'日均差':>8}{'t(日)':>7}  {'题材日均差':>10}{'t(日簇)':>8}")
summary = {}
for key in family:
    day_diffs, theme_diffs_by_day, picks_total, theme_days, days_with = [], defaultdict(list), 0, 0, 0
    for d in days:
        picked = d["tactics"].get(key)
        if not picked: continue
        rest = [r["held_close"] for s, r in d["rows"].items() if s not in picked and r["held_close"] is not None]
        if not rest: continue
        base = mean(rest)
        pr = [d["rows"][s] for s in picked if d["rows"][s]["held_close"] is not None]
        if not pr: continue
        days_with += 1; picks_total += len(pr)
        day_diffs.append(mean(r["held_close"] for r in pr) - base)
        by_theme = defaultdict(list)
        for r in pr: by_theme[r["sector"] or "?"].append(r["held_close"])
        theme_days += len(by_theme)
        for sector, vals in by_theme.items():
            theme_diffs_by_day[d["day"]].append(mean(vals) - base)
    if not days_with: continue
    t_day, _ = tstat(day_diffs)
    # theme-day differences, clustered by day: day mean of theme diffs -> t over days (cluster = day)
    cluster_means = [mean(v) for v in theme_diffs_by_day.values()]
    t_cluster, _ = tstat(cluster_means)
    allthemes = [x for v in theme_diffs_by_day.values() for x in v]
    summary[key] = {"days": days_with, "picks": picks_total, "theme_days": theme_days}
    print(f"{key:<30}{days_with:>6}{picks_total:>6}{theme_days:>7}{picks_total/theme_days:>10.2f}  {fmt(mean(day_diffs)):>8}{(t_day or 0):>7.2f}  {fmt(mean(allthemes)):>10}{(t_cluster or 0):>8.2f}")

# ---- #2 buyability ledgers ----
print("\n== #2 次日能不能买：下一交易日的开盘类型（选股 → S1） ==")
print(f"{'战法':<30}{'选股':>6}{'一字':>7}{'开盘涨停后开板':>12}{'未开在涨停':>10}{'无S1':>6}")
conv = {}
for key in family:
    n = one = reo = normal = none_ = 0
    r_ledger, r_a, r_b = [], [], []
    for d in days:
        for s in d["tactics"].get(key, []):
            r = d["rows"][s]; s1 = r["s1"]; n += 1
            if not s1 or s1["open"] is None or s1["close"] is None:
                none_ += 1; continue
            o2c = s1["close"] / s1["open"] - 1
            if s1["one_word"]:
                one += 1; r_a.append(0.0); r_b.append(0.0)               # cash, kept in the sample
            elif s1["reopened"]:
                reo += 1; r_a.append(s1["close"] / s1["limit_up"] - 1); r_b.append(0.0)  # A: filled at the limit once it opens
            else:
                normal += 1; r_ledger.append(o2c); r_a.append(o2c); r_b.append(o2c)
    if n:
        conv[key] = (n, len(r_ledger), mean(r_ledger) if r_ledger else None, mean(r_a) if r_a else None, mean(r_b) if r_b else None)
        print(f"{key:<30}{n:>6}{one/n*100:>6.1f}%{reo/n*100:>11.1f}%{normal/n*100:>9.1f}%{none_:>6}")
print("\n== 次日买入→收盘，三种记账（每笔均值）：账本（只记能买的）/ A 连板网（开板后按涨停价成交，一字记 0 不删）/ B 保守（只要开盘涨停就记 0） ==")
print(f"{'战法':<30}{'选股':>6}{'账本(n)':>9}{'账本':>9}{'A':>9}{'B':>9}")
for key, (n, nl, ml, ma, mb) in conv.items():
    print(f"{key:<30}{n:>6}{nl:>9}{fmt(ml):>9}{fmt(ma):>9}{fmt(mb):>9}")

# ---- reference: whole pool ----
allrows = [r for d in days for r in d["rows"].values()]
one = sum(1 for r in allrows if r["s1"] and r["s1"]["one_word"]); reo = sum(1 for r in allrows if r["s1"] and r["s1"]["reopened"])
print(f"\n全部选股行 {len(allrows)}：次日一字 {one/len(allrows)*100:.1f}%，开盘涨停后开板 {reo/len(allrows)*100:.1f}%")

# ---- overnight vs intraday split of the next session, picks vs the rest of the pool ----
print("\n== 次日收益拆分：隔夜（S1开盘/今收-1）与盘中（S1收盘/S1开盘-1），选股均值 vs 当日其余均值；n 为有 S1 的笔数 ==")
print(f"{'战法':<30}{'n':>6}{'隔夜 选股':>10}{'隔夜 其余':>10}{'盘中 选股':>10}{'盘中 其余':>10}{'合计 选股':>10}{'合计 其余':>10}")
def parts(r):
    s1 = r["s1"]
    if not s1 or not s1["open"] or not s1["close"] or not s1["pre"]: return None
    return (s1["open"] / s1["pre"] - 1, s1["close"] / s1["open"] - 1, s1["close"] / s1["pre"] - 1)
for key in family:
    P, R = [], []
    for d in days:
        picked = set(d["tactics"].get(key, []))
        if not picked: continue
        for s, r in d["rows"].items():
            p = parts(r)
            if p: (P if s in picked else R).append(p)
    if len(P) < 30: continue
    cols = [(mean(x[i] for x in P), mean(x[i] for x in R)) for i in range(3)]
    print(f"{key:<30}{len(P):>6}{fmt(cols[0][0]):>10}{fmt(cols[0][1]):>10}{fmt(cols[1][0]):>10}{fmt(cols[1][1]):>10}{fmt(cols[2][0]):>10}{fmt(cols[2][1]):>10}")
