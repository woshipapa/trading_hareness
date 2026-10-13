# A 股打板 / 竞价选股规则清单（短线来财 v6.18.0）

来源根目录：`.../duanxian_laicai_6.18.0/electron/`（只读，未运行任何代码）。
下文 `file:line` 均相对该根目录。阈值以代码为准；desc/注释与代码不一致处标「**差异**」。
用途：项目 backlog「待实现策略」。本清单不含授权/激活相关内容。

## 0. 通用约定（影响所有规则）

- **候选池来源**：`services/engine.js:227-243 tickPools()` 每 `POLL.pools`=20000ms（`config.js:72-79`）拉东财 `push2ex.eastmoney.com/getTopicZTPool|DTPool|ZBPool`，`pagesize=220`、`sort=fbt:asc`（涨停/炸板，按首封时间升序）、`dt` 为 `fund:asc`（`services/eastmoney.js:111-125`）。`ctx.zt.items` 即涨停池，`ctx.zt.total` 为接口返回的 `tc`（未过滤总数）。
- **池字段映射**（`services/eastmoney.js:88-109 normalizePoolItem`）：`boardTimes`=`lbc`（缺则 `zttj.ct`，再缺 0）；`firstSealTime`=`fbt`（HHMMSS 整数）；`lastSealTime`=`lbt`；`fund`=`fund`（封单额，元）；`brokenTimes`=`zbc`；`floatCap`=`ltsz`（元）；`turnover`=`hs`（%）；`amount`；`price`=`p/1000`；`industry`=`hybk`（缺为 `'—'`）；`lastLimitUp`=`zttj.days`（无则 0）。
- **passFilter**（`services/strategy.js:17-25`，默认全开 `config.js:24-29`）：`excludeGEM` 代码 `/^(30|301)/`；`excludeSTAR` `/^(688|689)/`；`excludeBJ` `/^(4|8|9)/`（含 9 开头）；`excludeST` 名称 `/ST|退|\*/`。`filterPool` = `(pool||[]).filter(passFilter)`（`strategy.js:27`）。
- **sealStrength(s)** = `floatCap>0 ? fund/floatCap : 0`（`strategy.js:30`）。
- **best(list, scorers)**（`strategy.js:36-55`）：字典序比较，`fn(x)||0`，严格大于才替换；**完全并列时保留池中先出现者 = 首封时间更早者**（因 API 按 fbt 升序）。
- **rankBy**（`strategy.js:302-313`）：同样字典序，降序，`Array.sort`（稳定），并列保持池序。
- **boardMatch(a,b)**（`strategy.js:58-70`）：`normBoardName` 去空白/括号/`·、-`，再去尾缀 `(行业|概念|板块|制造|设备|服务|产业|指数|开发|加工)$`；相等或互相 `includes` 即匹配。
- **industryBoards**：东财行业板块榜，`fs=m:90+t:2+f:!50`，`size=40`，`fid=f3`（按涨幅降序）（`eastmoney.js:132,171-172`）。`upCount`=f104、`downCount`=f105（板块内上涨/下跌**家数**）、`leader`=f128、`leaderCode`=f140、`leadPct`=f136。新浪兜底源这两项为 `null`（`services/sina.js:50-51`）。
- ctx 构成：`engine.js:259-268 recompute()`：zt/dt/zb/industryBoards/conceptBoards/gainers/filter。`gainers` 已 passFilter（`engine.js:248-256`）。
- 调用链：`buildTactics(ctx)`（`strategy.js:491-510`）遍历 `config.TACTICS`；`buildBayi(ctx)`（`strategy.js:450-487`）遍历 `config.BAYI`；规则抛错则该槽「规则异常」。

---

## 打板战法槽位 (strategy.js RULES)

`config.js:32-42` 的 `TACTICS` 实际 **9 个**（注释 `config.js:31`、`strategy.js:5`、`package.json` description、`src/index.html:133` 均写「十路战法」）。`RULES` 同为 9 个键（`strategy.js:89-293`）。输出字段统一为 `{stock, position:'holding'|'empty', reason, metrics[], signals[], alert}`；无标的返回 `empty(reason)`（`strategy.js:512-514`）。`alert` 为规则内硬编码字符串，**不参与筛选**。池均为**涨停池**（`ctx.zt.items`）经 `filterPool`，除 q7k 同时用 `industryBoards`。

| # | id | 展示名 (config) | 代码 | file:line |
|---|----|----|----|----|
| 1 | cjv2 | 战法二-程咬金v2 | QMT1.7 | strategy.js:90-107 |
| 2 | q10d | 战法三-Q10-d | Q10-d | strategy.js:109-126 |
| 3 | q10a | 战法三-Q10-a | Q10-a | strategy.js:128-147 |
| 4 | q8 | 战法三-Q8封神 | Q8 | strategy.js:149-166 |
| 5 | q7k | 战法三-Q7k主 | Q7-k | strategy.js:168-195 |
| 6 | pangyue | 战法四胖月 | PAN-Y | strategy.js:197-216 |
| 7 | baimao | 战法五白猫 | WHITE | strategy.js:218-237 |
| 8 | qilin | 战法五麒麟 | QILIN | strategy.js:239-269 |
| 9 | v66 | 战法十-V6.6 | v6.6 | strategy.js:271-292 |

每条规则只输出**一只**标的（best 的赢家）。

### 1. cjv2 程咬金v2（strategy.js:90-107）
- 池：涨停池 + passFilter，`boardTimes <= 1`（含 0）。
- 条件：无额外阈值。
- 排序（字典序，取第一）：①`sealStrength` 降序；②`turnover > 3 ? 1 : 0`。②只在 sealStrength 精确相等时起作用，实际近似无效。
- 输出：reason「首板封单强度榜首」；metrics 封单强度%、连板、首封 hm；signals `['首板','强封','量价配合']`（静态，不校验量价）；alert「早盘 9:30–10:00」。无标的：「今日无首板标的」。

### 2. q10d 二板加速（strategy.js:109-126）
- 池：`boardTimes === 2`。
- 排序：①`fund` 降序；②`brokenTimes===0 ? 1e8 : 0`（①并列才生效，实际近似无效）。
- 输出：reason 固定写「二板主升 · 封单 X · 零炸板」——**零炸板为静态文案，不保证 brokenTimes=0**（差异：注释/文案 vs 代码）。metrics 连板「2 板」、封单、换手；signals `['二板','零炸板','行业:'+industry]`；alert「早盘 9:30–10:30」。

### 3. q10a 早板（strategy.js:128-147）
- 池：`boardTimes <= 1 && firstSealTime >= 93000`（排除 9:30 前封板，即竞价一字不入）。
- 排序：`1e9 - firstSealTime` 降序 = 首封时间最早。
- 输出：reason「首封 hh:mm · 早盘抢筹成功」；metrics 首封、封单、行业；alert「早盘 9:30–9:45」（仅文案，代码不限制首封上界，全天最早的 >=9:30 的首板都会入选）。

### 4. q8 Q8封神 高标（strategy.js:149-166）
- 池：`boardTimes >= 4`。
- 排序：①`boardTimes`；②`fund/1e8`。
- 输出：reason「全市场 N 连板高标」；metrics 连板、累计=`lastLimitUp || boardTimes` 日、封单；alert「尾盘 14:30–15:00」。无标的：「今日无四板以上高标」。

### 5. q7k 主线首板（strategy.js:168-195）
- 池：涨停池 + `boardTimes <= 1` + 所属行业匹配 `industryBoards` 前 n 名（`boardMatch`）。
- **n 的回退**：依次试 `n ∈ [3, 8, 15]`（`strategy.js:173`），第一个池非空即停；`used` 记录实际 n。（差异：注释写「板块涨幅前三」，代码最多放宽到前 15。）
- 排序：①`sealStrength`；②`amount/1e8`。
- 输出：reason「主线【industry】首板 … （板块前 used）」；metrics 主线、封单、成交；alert「早盘 9:30–10:00」。无标的「主线板块暂无首板」。依赖 `industryBoards` 为空则池为空。

### 6. pangyue 胖月 回封（strategy.js:197-216）
- 池：`brokenTimes >= 1 && lastSealTime > firstSealTime`。
- 排序：①`fund`；②`-brokenTimes`（炸得少者优先，①并列才生效）。
- 输出：reason「炸板 N 次后于 hh:mm 回封」；metrics 炸板、回封时间、封单；alert「盘中 10:00–14:30」。

### 7. baimao 白猫 低价板（strategy.js:218-237）
- 池：`boardTimes <= 1 && price > 0 && price < 10`。
- 排序：①`fund`；②`-price`。
- 输出：reason「低价 x.xx 元首板」；metrics 股价、封单、换手；alert「早盘 9:30–10:00」。

### 8. qilin 麒麟 板块龙头（strategy.js:239-269）
- 步骤：对 filterPool 后的涨停池按 `industry` 计数（跳过空与 `'—'`）；取计数最大的行业为 `hotIndustry`（并列取 Map 先插入者，即池序中先出现者）；池 = 该行业全部涨停股（不限板数）。
- 排序：①`boardTimes`；②`fund`。
- 输出：reason「最强板块【X】N 家涨停 · 身位龙头」；metrics 板块涨停、连板、封单；alert「盘中 10:00–14:30」。无行业：「尚未形成板块效应」。

### 9. v66 V6.6 总龙头（strategy.js:271-292）
- 池：全部 filterPool 涨停股；`maxBoard = max(boardTimes)`；池 = `boardTimes === maxBoard`。
- 排序：①`amount`；②`fund`。
- 输出：reason「N 连板 · 成交 X · 全场人气核心」；metrics 连板、成交、换手；alert「尾盘 14:30–15:00」。无涨停：「今日无涨停标的」。

---

## 八阵图 (BAYI_RULES)

`config.js:44-54` `BAYI` 共 **8 槽**，`BAYI_RULES` 8 个键（`strategy.js:315-448`）。每槽返回 `candidates`（已按优先级降序的**全部**候选）+ `reasonFor`/`metricsFor`/`signals`；最终由 `buildBayi` 去重选一只。

**去重规则**（`strategy.js:450-487`）：按 BAYI 顺序依次处理；每槽选 `candidates.find(c => !used.has(c.tsCode || c.code)) || candidates[0]`（优先未被前面槽位占用者；全被占用则复用该槽第一名）；选中后 `used.add(tsCode||code)`。`unique = candidates.length > 1`。无候选：stock=null、reason「暂无符合条件标的」。（注意：先算所有槽的 rule 再顺序去重，去重只防**后面的槽**撞前面。）

| # | id | label | config desc | 池 | 过滤 | 排序键（降序，依次） | file:line |
|---|----|----|----|----|----|----|----|
| 1 | qianlong | 潜龙 | 首板 · 封单强度最强 | 涨停池 | `boardTimes<=1` | sealStrength, fund | strategy.js:316-328 |
| 2 | cixin | 次新 | 新晋强势 · 高换手首板 | 涨停池 | `boardTimes<=1` | turnover, amount | strategy.js:330-342 |
| 3 | jiancang | 建仓 | 首板 · 大额封单净流入 | 涨停池 | `boardTimes<=1` | fund, amount | strategy.js:344-356 |
| 4 | zhangting | 涨停 | 边缘界 · 高标连板 | 涨停池 | 无 | boardTimes, fund | strategy.js:358-370 |
| 5 | caishouyi | 财-收益最高 | 成交额最大 · 资金抱团 | 涨停池 | 无 | amount, turnover | strategy.js:372-384 |
| 6 | zhangmax | 涨-停最大 | 当日涨幅最强 | `ctx.gainers`（全市场涨幅榜，已 passFilter 两次） | 无 | changePct, amount | strategy.js:386-398 |
| 7 | gqjc | G-抢节奏 | 封板率最高板块领涨 | `industryBoards`（需 leaderCode 且领涨股过 passFilter） | 见下 | (upCount-downCount), changePct | strategy.js:400-433 |
| 8 | hhlb | H-恒率连板 | 连板天数最多 | 涨停池 | `(lastLimitUp||0) >= 2` | lastLimitUp, boardTimes | strategy.js:435-447 |

槽位输出：
- qianlong：reason「首板封单强度第一 · 封单/流通 x%」；metrics 封单/流通、封单；signals `['潜龙','首板','强封']`。
- cixin：reason「换手 x% 居首」；metrics 换手、首封；signals `['次新','高换手','首板']`。**差异**：槽名「次新」，代码**不判断次新股/上市天数**，仅为涨停池首板里换手最高者。
- jiancang：reason「封单绝对额居首」；metrics 封单、流通市值。**差异**：desc 写「大额封单净流入」，代码用 `fund`（封单额），无净流入。
- zhangting：reason「N 连板 · 全市场身位最高」；与 q8/v66 同类但无 ≥4 板门槛。
- caishouyi：reason「成交额 X · 资金抱团」；池仅涨停池（不是全市场成交额榜）。
- zhangmax：reason「当日涨幅 +x%」；池是 `ctx.gainers`（东财涨幅榜，条数由 tickBoards 决定，未在本次范围内核实）。
- gqjc：候选 = 按 `(upCount||0)-(downCount||0)` 降序、再 `changePct` 降序排序的板块的领涨股；构造的 stock `tsCode: b.tsCode`（**板块 BK 代码，不是个股**），boardTimes=0、amount/floatCap/turnover=0、price=0。**差异 1**：desc 写「封板率最高板块」，代码用的是板块「上涨家数−下跌家数」，无任何封板率/涨停家数。**差异 2**：metrics 标签「板块涨停 N 家」实际显示 `upCount`（上涨家数）。**差异 3**：dedup 键用板块 tsCode，因此不会与其它槽的同一只个股去重。新浪兜底源 upCount/downCount 为 null → 退化为仅按 `changePct`。reason「最强板块【X】领涨股」。
- hhlb：reason「连续 N 日涨停 · 恒率连板」；metrics 连续、连板。**差异**：`lastLimitUp` 来自 `zttj.days`（东财「涨停统计 days」），代码当作「连续涨停天数」，该字段的精确含义无法从代码确认（见「不确定」）。

---

## 情绪温度

`emotionScore({zt, dt, zb, breadth, prevZTPerf})`（`strategy.js:533-547`）：

```
limitUp   = zt.total  || 0          // 接口 tc，未经 passFilter
limitDown = dt.total  || 0
broken    = zb.total  || 0
sealRate  = (limitUp+broken>0) ? limitUp/(limitUp+broken) : 0
upRatio   = (breadth && breadth.total) ? breadth.up/breadth.total : 0
score = 50
score += min(limitUp,100)  * 0.28
score -= min(limitDown,40) * 0.6
score += (sealRate - 0.7)  * 40
score += (upRatio  - 0.5)  * 60
if (prevZTPerf != null) score += prevZTPerf * 3.2
return clamp(round(score), 0, 100)
```

- `prevZTPerf` 实参 = 昨日涨停池全部个股今日**平均涨跌幅(%)**（`engine.js:411-424 aggregatePrevZT` 的 `avg`，`engine.js:435`）；昨日池向前逐日回找最多 12 个自然日取第一个 total>0（`engine.js:392-408`）。
- 无 breadth 时 `upRatio=0` 会使分数减 30（`(0-0.5)*60`）；无涨停池数据时 `sealRate=0` 减 28。
- `emotionLabel(score)`（`strategy.js:549-555`）：`>=80` 情绪高潮(up)；`>=62` 情绪回暖(up)；`>=45` 情绪中性(neutral)；`>=30` 情绪退潮(down)；其余 情绪冰点(down)。
- 刷新：breadth 每 `POLL.breadth`=60000ms，pools 20000ms（`config.js:72-79`）。

---

## 竞价战法 (auction-strategy.js)

`STRATEGIES`（`auction-strategy.js:637-649`）共 **11** 个，顺序即执行顺序。`run()`（`:656-691`）：对每个战法 try/catch，成功则追加 `alertTime = ALERT[alert]`、`alertKey`，并给每行补 `price`/`changePct`（`withPrice`，`:666-674`）；失败返回 `{error:true, note:'执行失败：…'}`。

**入口数据**：`main.js:1034-1075 auction:analyze` → `factors.enrich(snap, {prevZt:hist.zt, prevZb:hist.zb, prevZtInfo:hist.zt})` 后的行（含 `auctionPct/amtRatio/bidAskRatio/vwapImbalance/...`，见因子一节）。行集 = 当日 `auction/<date>.json` 快照全部行（universe 已剔除 30/68/4/8/9 开头与 ST/退，`auction.js:62-71`）。

**涨停历史 `history(days=3)`**（`auction-strategy.js:60-95`）：交易日历 `limitupTable.calendar()` 取 `< 今天` 的最近 3 个交易日；对每日拉涨停池（`ztOf`，带内存缓存 `ztCache`，失败缓存空 Map）；同一只票**以最近一次涨停为准**，记录 `{boardTimes(缺则1), industry, date, back, fresh}`，`back` = 距最近交易日的天数(0=昨日)，`fresh = [100,70,50,35][back] || 30`（back 只会是 0/1/2 → 100/70/50）。`zb` = **仅最近一个交易日**的炸板池。
> 凡下文说「昨日涨停」而代码用 `hist.zt` 的，实际是「近 3 个交易日内涨停过」，不校验 `back===0`。

**ALERT 窗口**（`:629-635`，仅展示用，不影响筛选）：`pre`盘前 09:15-09:25（**未被任何战法使用**）；`auc`竞价 09:20-09:25；`open`早盘 09:30-10:00；`mid`盘中 10:00-14:30；`tail`尾盘 14:30-15:00。

**分项评分函数**（`:99-142`）：
- `scoreAuctionStrength(pct)`：非数/ <0 →0；<2→20；<5→50；<7→85；<9→100；<9.8→80；其余 60。
- `scoreSellPressure(baRatio)`（baRatio=委买/委卖）：非数或 <=0 →30；`r=1/baRatio`(=委卖/委买)：<0.5→60；<1→30；<=3→100；<=5→60；否则 10。
- `scoreCap(floatCapYuan)`：亿 <=50→100；<=100→80；<=200→60；<=300→40；否则 0；非数 0。
- `scoreBoard(b)`：>=5→100；4→92；3→80；2→60；1→30；其余 0。
- `WEIGHTS`（`:145-151`）：board 20、strength 15、pressure 10、cap 5、fresh 5，`WEIGHT_TOTAL=55`。`MISSING`：龙虎榜席位 15、行为因子 30（未接入）。

### 1. yaogu 妖股竞价六条件（`sYaoGu` :168-231；alert=`auc` 竞价 09:20-09:25；heavy）
- need（声明）：`本地竞价快照 + 近三日涨停池`。source：GitHub xingyuantaoci/yuyijin。
- 硬条件（依次，不满足计入 rejected）：①`hist.zt` 含该票（近 3 交易日有涨停）；②`turnover` 有限且 `> 2`；③`auctionPct` 有限且 `> 5`；④`askSum > bidSum`；⑤`floatCap` 有限且 `floatCap/1e8 <= 300`。
- 评分：`got = Σ parts[k].score * w/100`；`score = got / 55 * 100`（保留 1 位）。parts：board=`scoreBoard(info.boardTimes)`(w20)、strength=`scoreAuctionStrength(auctionPct)`(w15)、pressure=`scoreSellPressure(bidAskRatio)`(w10)、cap=`scoreCap(floatCap)`(w5)、fresh=`info.fresh`(w5)。
- 排序：`score` 降序，取前 40（`:224`）。
- **差异**：①名称「六条件」，desc/代码实际只有 **5 条**硬条件；②文件头写「7 因子加权评分」，代码 5 项（龙虎榜、行为因子缺）；③desc 与 rejected 标签写「总市值 ≤ 300 亿」，代码用的是**流通市值 `floatCap`**；④`askSum > bidSum` 恒使 `1/baRatio > 1`，`scoreSellPressure` 的 `<0.5`、`<1` 两档不可达；⑤`bidSum=0` 时 `bidAskRatio=0` → 走「<=0 →30」而非 10；⑥东财兜底行 `bidSum=askSum=0`（`auction.js:257-258`）永远过不了条件④。

### 2. grab 竞价抢筹（`sGrab` :234-277；alert=`auc`）
- need：`本地竞价快照`。source：Tushare stk_auction 实战总结。
- 条件：`volRatio` 有限且 `> 1.5`；`auctionPct` 有限且 `>= 2` 且 `<= 5`；`amount` 有限且 `> 5e7`（5000 万）。
- 分数：`min(1,(volRatio-1.5)/3)*33 + min(1,(auctionPct-2)/3)*33 + min(1,amount/5e8)*34`。
- **排序：按 `amount` 降序**（不是按分），前 40。**差异**：展示列「抢筹分」与排序键不一致。desc 与条件一致。

### 3. weak 竞价弱转强（`sWeakToStrong` :280-326；alert=`open` 早盘 09:30-10:00；heavy）
- need：`本地竞价快照 + 昨日涨停池/炸板池`。
- 条件：`zb = hist.zb.get(code)`（最近一交易日炸板池）；`zt = hist.zt.get(code)`（近 3 交易日涨停）；两者皆无则剔除；`auctionPct` 有限；若 `isZb`（优先判炸板）：`pct >= 3`；否则 `pct >= 0`。
- 分数：`min(100, max(0,pct)*10)*0.6 + min(100, (amtRatio有限?amtRatio:0)*1.5)*0.4`。
- **排序：按 `auctionPct` 降序**（不是按分），前 40。kind 文案 `昨日炸板` / `昨日${boardTimes}板`。
- **差异**：desc「昨日涨停股」，代码 `hist.zt` 实为近 3 日窗口；一只票既在 zt 又在昨日炸板池则按炸板口径(≥3%)。

### 4. oneword 一字 / 秒板预备（`sOneWord` :329-357；alert=`open`）
- need：`本地竞价快照`。
- 条件：`auctionPct` 有限且 `>= 9.8`。
- 排序：`amtRatio` 降序（缺为 0），前 40；`score = amtRatio`（‱）。reasons 区分 `isOneWord`（`price >= limitUp-1e-6`）。
- 一致。注意阈值 9.8 为 10% 板假设；范围内无 20%/30% 板票（已被 universe 剔除）。

### 5. imbalance VWAP 失衡（`sImbalance` :360-394；alert=`auc`）
- need：`本地竞价快照（含五档）`。source：GitHub AndyMeng2002/5210-Assignment（五档加权均价代理）。
- 条件：`vwapImbalance` 有限且 `> 0.5`，且 `amtRatio` 有限且 `> 2`。
- 排序：`vwapImbalance` 降序，前 40；`score = vwapImbalance`。
- 公式见因子 `vwapImbalance`。**差异/风险**：reasons 文案写「委买均价高于竞价价、委卖均价低于竞价价」，但代码只检查比值 `> 0.5`；分子分母同为负（买均价 < 价格 且 卖均价 > 价格，即常见的盘口形态）也得正值，会通过。需实现时明确符号约束。

### 6. mainforce 竞价资金主攻（`sMainForce` :397-427；alert=`auc`）
- need：`本地竞价快照`。
- 条件：`amount` 有限且 `> 0`。`total = Σ amount`；按 `amount` 降序前 **30**；`share = amount/total*100`（%），`score = share`。无阈值。一致。

### 7. sanyaosu 竞价三要素（`sSanYaosu` :436-486；alert=`open`；heavy）
- need：`本地竞价快照`。source：超越小明《竞价情绪》。
- 条件：①`auctionPct` 有限且 `> 0`；②`auctionPct >= 3`；③`(volRatio 有限 且 >= 1.2) 或 (amtRatio 有限 且 >= 3)`。
- 分数：`min(100, auctionPct/7*100)*0.5 + min(100, (volRatio||1)/3*100)*0.3 + min(100, (amtRatio||0)/30*100)*0.2`。
- 排序：`score` 降序，前 40。附带 `hist.zt` 的 boardText/sector（仅展示，不参与筛选）。
- **差异**：desc/注释「竞价分时不下水直接排除」——代码**没有竞价分时数据**，只用最终 `auctionPct > 0` 近似，无法判断「下水」。

### 8. shouban 首板 · 一进二（`sShouban` :498-557；alert=`open`；heavy）
- need：`本地竞价快照 + 近三日涨停池`。source：《游资密录：首板、一进二》。
- 条件：`hist.zt` 含该票；`bt = info.boardTimes||1 <= 2`；`auctionPct` 有限且 `>= 1`；`capYi = (floatCap||0)/1e8 <= 300`（floatCap 缺失视为 0 → 通过）。
- 分数：`max(0, min(100, pct/6*100)*0.45 + min(100, volOk/2.5*100)*0.25 + max(0, 100 - capYi/1.5)*0.3 + volPenalty)`，`volOk = volRatio有限?volRatio:1`；`volPenalty = (bt===2 && volOk>3) ? -15 : 0`（直接减 15 分，不乘权重）。
- 排序：`score` 降序，前 40。
- **差异**：desc「昨日首板/二板」，代码为近 3 个交易日内最近一次涨停时的 boardTimes≤2，不要求是昨日。

### 9. fanpao 龙头反包（断板）（`sFanpao` :564-576 → `services/fanpao.js:72-183`；alert=`open`；heavy）
- 声明 need：`前日涨停池（连板数）+ 近 14 日日 K + 当日快照现价`。参数 `minLianban=3`（`auction-strategy.js:565`）。
- 流程：交易日历 `cal`，要求 `cal.length>=3`；`d2 = cal[len-3]`（前天），`d1 = cal[len-2]`（昨天）（以日历最后一个元素为「今天」/最近日，见不确定）。候选 = `d2` 涨停池中 `boardTimes>=3`，按 boardTimes 降序取前 `MAX_FETCH=40`。
- 每只条件：当日快照有该票；日 K（`ztHistory.klineOf(code,14)`，`fanpao.js:110`）≥3 根且含 d1、d2 两根；断板确认 `k1.changePct < LIMIT_PCT(9.8)`；`price`（快照现价）>0；`high = k1.high > 0`；`above = (price/high-1)*100 > 0`（现价突破断板日高点）。
- 分数：`sBoard=min(100,50+(bt-3)*10)`；`sAbove = above<=1 ? above*40 : above<=5 ? 40+(above-1)*10 : max(20, 80-(above-5)*8)`；`sPost = clamp(50 + d1Pct*4, 0, 100)`；`score = clamp(sBoard*0.45 + sAbove*0.3 + sPost*0.25, 0, 100)`。
- 排序：`score` 降序（无截断）。出场参数（仅输出，不参与筛选）：`stopLoss=-7`，`takeProfit = takeProfitOf(bt)`：bt>=5→30，4→25，3→20，其余 15（`fanpao.js:44-50`）。
- 差异：无（desc「开板止盈 + 7% 止损 + 3板20%/4板25%/5板+30%」与代码一致；「开板止盈」仅是文案，代码未实现）。

### 10. dixi 强势股低吸（回踩）（`sDixi` :582-594 → `services/dixi.js:118-251`；alert=`mid` 盘中 10:00-14:30；heavy）
- 参数（`auction-strategy.js:583`）：`minBoard=1, fallMin=1, fallMax=12`；`LOOKBACK=5`（`dixi.js:29`）；声明 need：`近 5 日涨停记录 + 近 40 日日 K + 当日快照现价`。
- 候选：近 5 个交易日涨停（优先 `ztHistory.load()` 本地库 `days[d].ztItems`，否则东财涨停池）；同票取板数更大者；须在当日快照；`board >= minBoard`；按 board 降序取前 `MAX_FETCH=40`。
- 条件（依次，满足全部）：日 K 条数 ≥25（剔除当日未走完那根后 ≥22）；找到涨停日 K（`ztBar`）；`price>0`；`fall = (price/ztClose-1)*100`，须 `-12 <= fall <= -1`（`fall > -1` 剔除；`fall < -12` 剔除）；`price >= startLow*0.98`（`startLow` = 涨停日最低价，留 2% 容差）；`ma10` 有效且 `price >= ma10*0.97`；`ma20` 有效时 `price >= ma20*0.92`；`volRatio5 = 昨量/5日均量`，若有则须 `<= 1.0`；最近 3 根日 K 中跌幅 `< -5%` 的根数须 `< 2`。
- 分数：`sFall = max(0, 100 - |fall|*6.7)`；`sVol = (volRatio5==null) ? 50 : max(30, min(100, 130 - volRatio5*100))`；`sBoard = min(100, 40+(board-1)*20)`；`cushion=(price/startLow-1)*100`、`sSafe = clamp(cushion*8, 0, 100)`；`score = (sFall+sVol+sBoard+sSafe)*0.25`。
- 输出：买区间 `buyLow=ma10`、`buyHigh=max(ma5,ma10)`；`stop = min(startLow*0.97, (ma10||startLow)*0.97)`。
- **差异**：desc「缩量（<5 日均量）」代码 `volRatio5 <= 1.0`，即不要求更小；注释写「量能 < 5 日均量 85%」但代码阈值 1.0；desc「止损放启动位下方 3%」与 `stop` 公式一致但取了 `min(…)`；desc「守住 MA10」代码允许 3% 容差。`fall` 以涨停日**收盘价**为基准，不是涨停价。

### 11. trend 趋势跟踪（ATR 通道）（`sTrend` :601-613 → `services/trend.js:77-208`；alert=`tail` 尾盘 14:30-15:00；heavy）
- 参数：`top=50`、`capital=100000`、`riskPct=1`（`auction-strategy.js:602`，`opts` 缺省）；声明 need：`近 90 日日 K + 当日快照现价`。
- 候选：快照中 `price>0` 的行按 `amount` 降序取前 50。
- 条件：日 K ≥65 根（剔除当日未走完后 ≥62）；`ma20`、`atr14` 有效且 `atr14>0`；**`price > ma20`**（现价）；**`ma5 > ma20`**；`stop = min(close-2*ATR14, ma20)` 必须 `< price`。
- 分数：`sStack` 标准多头(ma5>ma10>ma20)=100 / `ma5>ma20`=60 / 其余 30；`sSlope = slope20==null?40:clamp(40+slope20*20, 10, 100)`（`slope20` = MA20 相对 5 日前的涨幅%）；`sPos`（`aboveMa60=(close/ma60-1)*100`）：null→50；>40→30；否则 `clamp(100-|aboveMa60-10|*3, 20, 100)`；`sAtr`（`atrPct=ATR14/close*100`）：<1.5→40；<=5→100；<=8→60；否则 25；`sVol = vRatio==null?50:clamp(50+(vRatio-1)*60, 20, 100)`（`vRatio=昨量/近20日均量`）；`score = sStack*0.3 + sSlope*0.25 + sPos*0.15 + sAtr*0.15 + sVol*0.15`。
- 仓位：`position.suggest({capital, entry:price, stop, atrPct, riskPct, maxSinglePct:25})`（`position.js` 未读，见不确定）。
- **差异**：desc「站上 MA20 + MA5 在 MA20 上方 + 均线多头排列 → 趋势成立」，代码**不强制**多头排列(ma5>ma10>ma20)，也不强制 MA20 斜率向上（`trend.js` 头注释写「MA20 斜率向上过滤」，实际只入分）。

---

## 因子 (factors.js)

`FACTORS`（`factors.js:33-82`）共 **12** 个；`dir`：1 越大越好，-1 越小越好。`enrichRow(r, ctx)`（`:96-155`）一次算出 A 层，C 层标记按 ctx 附加。输入行 `r` 来自 `auction.js parseQQLine`（`:149-199`）。

| key | 名称 | 层 | 单位/dir | 公式（代码） | 需要的输入 | file:line |
|---|---|---|---|---|---|---|
| auctionPct | 竞价涨幅 | A auction | %, 1 | `(price-preClose)/preClose*100`，preClose 缺则 null；round 2 位 | price、preClose | factors.js:35-37, 100, 129 |
| amtRatio | 竞价资金强度 | A | ‱, 1 | `amount/floatCap*10000`（floatCap 缺/0 则 null）；round 1 位 | amount(元)、floatCap(元) | :38-41, 101, 130 |
| volRatio | 量比 | A | 无, 1 | 直接取腾讯第 49 字段 `f[49]`（desc 写「当前分钟均量/过去5日均量」，代码仅透传）；东财兜底为 `f10` | volRatio | :42-45, 131; auction.js:181,246 |
| turnover | 竞价换手 | A | %, 1 | 直接取腾讯 `f[38]`（desc 写「竞价成交量/流通股本」，代码透传） | turnover | :46-49, 132; auction.js:180 |
| bidAskRatio | 委买委卖比 | A | 无, 1 | `askSum>0 ? bidSum/askSum : null`（五档合计，单位手） | bidSum、askSum | :50-53, 102, 133 |
| limitDist | 距涨停 | A | %, -1 | `(limitUp-price)/price*100`，limitUp 缺则 null | limitUp、price | :54-57, 103, 134 |
| capElastic | 市值弹性 | A | 亿, -1 | `floatCap/1e8`（`floatCap!=null`） | floatCap | :58-61, 104, 135 |
| vwapImbalance | VWAP 失衡 | A（元数据中位于 minute 项之间，layer 仍为 auction） | 无, 1 | `buyVwap=Σ(bidPx_i*bidVol_i)/Σ bidVol_i`（仅 p、v 有限且 v>0 的档），`sellVwap` 同理用卖五档；`(buyVwap-price)/(price-sellVwap)`，分母为 0 或任一 VWAP 缺则 null；round 3 位 | bidPx/bidVol/askPx/askVol(五档)、price | :70-73, 107-125, 136 |
| spreadStability | 价格变动稳定性 | B minute | 无, 1 | proxy：`deltas_i=|price_i-price_{i-1}|`；`mean/std`（std 为总体标准差，`sd>0` 才算）；至少 20 个有效分钟价，超过 250 取最后 250 | 腾讯分时逐分钟 price | :62-65, 242-253 |
| vwapExpansion | VWAP 扩张指数 | B minute | 无, 1 | proxy：`vwap = 最后一分钟 avg(>0) 否则 (max+min)/2`；`a=max(0,max-vwap)`，`b=max(0,vwap-min)`；`a>0&&b>0 ? a**b : 0`；round 4 位 | 腾讯分时 price/avg | :66-69, 255-265 |
| prevZtOpenPct | 昨涨停今竞价 | C cross | %, 1 | 若 `ctx.prevZtInfo` 含该票：`prevZtOpenPct = auctionPct`，并附 `prevBoardTimes`、`prevSector` | 近 3 日涨停历史(zt Map)、今日 auctionPct | :74-77, 146-151 |
| prevZbOpenPct | 昨炸板今竞价 | C cross | %, 1 | **元数据中声明，但全仓库代码无任何位置赋值此字段**（只设置 `wasZb`） | — | :78-81, 145 |

其它衍生字段（`enrichRow`）：`buyVwap`/`sellVwap`（round 3）、`isOneWord = limitUp ? price >= limitUp-1e-6 : false`（`:139`）、`limitUpSealPct = (limitUp-preClose)/preClose*100`（`:140`）、`wasZt`/`wasZb`（仅当 prevZt/prevZb 集合非空，`:144-145`）、`gapLevel`（`gapLevelOf(auctionPct)`：>=9.8 一字/接近涨停；>=7 大幅高开；>=3 高开；>0 小幅高开；>-3 小幅低开；>-7 低开；其余 大幅低开；非数 `—`，`:158-167`）。

聚合函数：
- `emotion(rows)`（`:192-234`）：按 gapLevel 分桶；`traded`(amount>0 家数)、`amountSum`；`up`(auctionPct>0)、`down`(<0)、`flat`；`oneWord`(auctionPct>=9.8)；`strongOpen`(auctionPct>=5)；`topAmt` 成交额前 12。
- `crossDay(rows, prevZt, prevZb)`（`:293-311`）：`prevZtCount`、`prevZtAvgOpen`、`prevZtPromote`(昨涨停今竞价>=5%)、`prevZtWeak`(<0)、`prevZbCount`、`prevZbAvgOpen`、`prevZbRepair`(昨炸板今竞价>=3%)。
- `rank(rows, factorId, {top=40, asc=false, min=0})`（`:183-189`）：**注意 `min` 默认 0 会过滤掉所有值为负的行**（含负的竞价涨幅）；主进程 `main.js:1130` 调用未传 min。
- B 层 `minuteFactors/minuteFactorsOf` 仅导出，仓库内（`electron/`、`src/`）未发现调用（grep 无结果），即 B 层因子当前**没有被任何战法或面板使用**。

---

## 每条规则需要的输入数据

时刻列：「盘中实时」= 依赖东财池/板块榜，交易时段 20s~60s 轮询（`config.js:72-79`），收盘后为当日终值；「9:26–9:31」= 依赖竞价快照，采集见下一小节。

| 规则 id | 读取的数据字段 | 数据源 | 需要的时刻 |
|---|---|---|---|
| cjv2 | 涨停池：boardTimes, fund, floatCap, turnover, firstSealTime（展示） | 涨停池 | 盘中实时（alert 文案 9:30–10:00） |
| q10d | 涨停池：boardTimes(=2), fund, brokenTimes, turnover, industry | 涨停池 | 盘中实时 |
| q10a | 涨停池：boardTimes, firstSealTime（>=93000）, fund, industry | 涨停池 | 盘中实时，9:30 后 |
| q8 | 涨停池：boardTimes(>=4), fund, lastLimitUp | 涨停池 | 盘中实时 |
| q7k | 涨停池：boardTimes, fund, floatCap, amount, industry；行业板块榜前 3/8/15 名名称 | 涨停池 + 行业板块榜(涨幅序) | 盘中实时 |
| pangyue | 涨停池：brokenTimes, firstSealTime, lastSealTime, fund | 涨停池 | 盘中实时 |
| baimao | 涨停池：boardTimes, price(<10), fund | 涨停池 | 盘中实时 |
| qilin | 涨停池：industry, boardTimes, fund | 涨停池 | 盘中实时 |
| v66 | 涨停池：boardTimes, amount, fund, turnover | 涨停池 | 盘中实时 |
| 八阵图 qianlong | 涨停池：boardTimes, fund, floatCap | 涨停池 | 盘中实时 |
| cixin | 涨停池：boardTimes, turnover, amount, firstSealTime | 涨停池 | 盘中实时 |
| jiancang | 涨停池：boardTimes, fund, amount, floatCap | 涨停池 | 盘中实时 |
| zhangting | 涨停池：boardTimes, fund | 涨停池 | 盘中实时 |
| caishouyi | 涨停池：amount, turnover | 涨停池 | 盘中实时 |
| zhangmax | 全市场涨幅榜：changePct, amount | 东财涨幅榜 | 盘中实时 |
| gqjc | 行业板块榜：upCount, downCount, changePct, leader/leaderCode/leadPct, name | 东财行业板块榜 | 盘中实时 |
| hhlb | 涨停池：lastLimitUp(>=2), boardTimes | 涨停池 | 盘中实时 |
| 情绪温度 | 涨停/跌停/炸板池 `total`；全市场涨跌家数 `breadth.up/total`；昨日涨停池个股今日平均涨跌幅 | 三池 + 市场宽度 + 昨日涨停池实时行情 | 盘中实时 |
| yaogu | 竞价：turnover, auctionPct, askSum, bidSum, bidAskRatio, floatCap；近 3 交易日涨停池 boardTimes/industry/新鲜度 | 竞价快照(含五档) + 历史涨停池 | 9:26–9:31 快照；历史前一日收盘后已确定 |
| grab | 竞价：volRatio, auctionPct, amount | 竞价快照 | 9:26–9:31 |
| weak | 竞价：auctionPct, amtRatio, volRatio, turnover, bidAskRatio；近 3 日涨停池(zt)、最近一日炸板池(zb) | 快照 + 历史池 | 9:26–9:31 |
| oneword | 竞价：auctionPct(>=9.8), amtRatio, isOneWord(price,limitUp) | 快照(需 limitUp) | 9:26–9:31 |
| imbalance | 竞价：五档 bidPx/bidVol/askPx/askVol → vwapImbalance；amtRatio | 快照(需五档，东财兜底行不含) | 9:26–9:31 |
| mainforce | 竞价：amount, auctionPct, amtRatio, volRatio, turnover, floatCap | 快照 | 9:26–9:31 |
| sanyaosu | 竞价：auctionPct, volRatio, amtRatio；历史涨停（仅展示） | 快照 + 历史池 | 9:26–9:31 |
| shouban | 竞价：auctionPct, volRatio, floatCap；近 3 日涨停池 boardTimes/industry | 快照 + 历史池 | 9:26–9:31 |
| fanpao | 前天(d2)涨停池 boardTimes>=3；昨天(d1)日 K：changePct, high；当日现价 price | 历史池 + 日 K(本地库 `zt-history`，缺则 `detail.fetchKline`) + 快照 price | 日 K 取 d1/d2 盘后已定；现价用快照（竞价时点为竞价价）；alert 为早盘 |
| dixi | 近 5 日涨停（board, date）；日 K 近 25~40 根：close, low, volume, changePct；当日现价 | 本地涨停历史库 / 东财池 + 日 K + 快照 price | 快照时点 + 日 K；alert 盘中 |
| trend | 快照 amount 取前 50；日 K 近 65~90 根：close/high/low/volume；现价 | 快照 + 日 K | 日 K 盘后；现价为快照；alert 尾盘 |

### 竞价快照采集（services/auction.js）
- **代码表**：新浪 `Market_Center` 分页枚举（`node=hs_a`，100 条/页，最多 80 页），缓存 `<store>/auction/universe.json` 有效期 7 天（`UNIVERSE_MAX_AGE`，`auction.js:73,79-123`）；`keepCode` 剔除非 6 位数字、`30*`、`68*`、`4/8/9*` 开头、名称含 `ST|退`（`:62-71`）。新浪全挂时 fallback 为 `600000..600999` 主板段（`:126-133`）。
- **快照源（主）**：腾讯 `https://qt.gtimg.cn/q=<tsCode,...>`，每批 50 只、并发 3、GBK、超时 11s、重试 1（`:137-211,282`）。解析字段：`f[1]` 名称、`f[2]` 代码、`f[3]` 价、`f[4]` 昨收、`f[5]` 今开、`f[6]` 量(手)、`f[9..18]` 买一~五(价,量)、`f[19..28]` 卖一~五、`f[30]` 时间、`f[33/34]` 高/低、`f[37]` 成交额(万→元)、`f[38]` 换手%、`f[44]` 流通市值(亿→元)、`f[45]` 总市值、`f[47]` 涨停价、`f[48]` 跌停价、`f[49]` 量比（`:140-199`）。价格为 0/缺则丢弃。
- **兜底**：腾讯漏掉的代码交东财 `ulist.np`（每批 40，字段 `f12,f14,f2,f3,f5,f6,f8,f10,f20,f21`），**无五档、无涨停价**；涨停价按 `昨收*1.1`（昨收由 `price/(1+pct/100)` 反推）四舍五入、跌停价 ×0.9（`:220-268`）。`bidSum/askSum=0`。
- **采集时刻**：默认三轮 `at` 标签 `09:26:20 / 09:27:20 / 09:31:00`，但 `at` 仅为标签，**实际间隔由 `gapMs` 决定**：第 1 轮立即，第 2 轮距上一轮结束后睡 60000ms，第 3 轮再睡 220000ms（`:424-445`）。调度（`main.js:581-617 scheduleAutoCapture`）：每天 `msUntil(9,26,20)` 触发，非交易日跳过（日历取 `limitupTable.calendar()`，失败则排除周末）；若启动时处于 9:26–9:35 则 3s 后补采，且用 `captureOnce`（一轮，不等待，`auction.js:462-464`）。`prefs.auctionAuto===false` 关闭。
- **合并规则**：`mergeSample`：同一代码取 `amount` 更大者，相等则后到者覆盖（`:318-330`）。
- **落盘**：`<userData>/auction/<YYYYMMDD>.json`（`{date, capturedAt, rounds, total, meta:{rounds, via, withBook}, rows}`）与同名 `.csv`（带 BOM，17 列：板块, 代码, 名称, 竞价涨幅%, 竞价价, 昨收, 涨停价, 竞价额(万), 竞价量(手), 量比, 换手%, 流通市值(亿), 委买(手), 委卖(手), 委比, 数据源, 采集时间）（`:334-384`；目录 `main.js:533-535 auctionStoreDir()`）。读取 `load(date)`、列表 `list()`、`latest()`。
- 战法/因子都是**面板打开时**才对本地文件现算（`main.js:1034 auction:analyze`），采集本身不打分（`main.js:527` 注释）。

---

## 不确定/读不出的地方

1. **采集的第 3 轮在 9:31 之后**：`mergeSample` 取 `amount` 更大者，而 9:30 后连续竞价的累计成交额只会 ≥ 竞价额，因此第 3 轮（约 9:31）几乎总是覆盖前两轮。最终快照的 `price/amount/vol/turnover/volRatio` 可能是 9:31 的连续竞价数据，而非 9:25 撮合结果；`auctionPct` 因此可能不是真实竞价涨幅。代码内未区分，也未存各轮原始数据。「once」模式则取启动时刻的单次快照。
2. **`hhlb` 的 `lastLimitUp`**：值来自东财 `zttj.days`，代码将其当「连续涨停天数」；`zttj.days` 的精确含义（统计窗口天数 vs 连续天数）无法从本代码确认。`boardTimes` 使用 `lbc`，与之并存。
3. **`zhangmax` 的涨幅榜条数**及排序由 `tickBoards`/`fetchGainers` 决定，未在本次范围内逐行核实。
4. **fanpao 的 d1/d2**：取 `limitupTable.calendar()` 的倒数第 2、3 个元素；若日历已包含「今天」则 d1=昨天，若日历未含今天则会偏移一天。`limitup-table.js` 未读，无法确认日历末尾语义。
5. **`ztHistory.klineOf(code, lmt)`**：本地缓存有 ≥20 根就整份返回，`lmt`（14/40/90）仅在联网拉取时生效；实际可用根数取决于本地库（注释称 121 根）。`ztHistory.load()` 本地涨停库、`position.suggest()` 的细节未读。
6. **`volRatio`/`turnover` 因子**：desc 的定义与代码（直接透传腾讯字段 49/38）不同；腾讯字段 38 在竞价时点是否为「竞价换手」未能验证（`f[37]` 的单位「万」亦出自代码注释）。
7. **`prevZbOpenPct`** 在代码中只有元数据声明，没有计算；`alert.pre`（盘前 09:15-09:25）无战法使用；B 层分钟因子无调用方。
8. 涨停池 `pagesize=220`：若当日涨停数 >220，`items` 被截断（而 `total` 为全量），情绪分用 total，选股用 items。
9. `strategy.js` 的 `alert` 时间窗口为展示文案，规则不据此过滤（除 q10a 的 `firstSealTime>=93000`）；规则运行时刻没有程序级限制，盘后也会用当日终值池计算。
10. 历史 `ztCache`（`auction-strategy.js:31-44`）对拉取失败的日期缓存空 Map，直至 `clearCache()`，可能让当日分析持续拿到空历史（调用点未全部核实）。
