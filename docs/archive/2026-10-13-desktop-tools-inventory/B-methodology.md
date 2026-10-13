# B. 交易规则模块清单（electron/services）

范围：`/Users/papa/codebase/n8n/state/research/duanxian_laicai_6.18.0/electron/services/` 下 12 个文件，全部通读，只读未运行。
约定：行号均指该文件内行号；"阈值"一律取代码行为，注释与代码不一致处标 **【注释≠代码】**。涨停统一用 `changePct >= 9.8`（`LIMIT`/`LIMIT_PCT`，不分板块），跌停 `<= -9.8`。
未读文件（被引用但不在本次范围）：`zt-history.js`（`klineOf/readKline/itemsOn/leadersOn/load/universe`）、`limitup-table.js`（`calendar`）、`eastmoney.js`、`strategy.js`、`quote.js`、`auction.js`、`config.js` 除 POLL 外。凡依赖它们语义处在末节注明。

---

## buydian.js（八买点）

### 判定范围
8 个买点只有 **3、6、8 在代码里判定**（`computable:true`，buydian.js:90,121,143）；1/2/4/5/7 仅作目录条目（`computable:false`，68-138），`check()` 对它们返回 `fails:['买点 N 不做日线判定']`（313）。

### 目录条目（仅列出，不判定）
| id | 名称 | 行 | 文件自述不判定的原因 |
|---|---|---|---|
| 1 | 主线雏形·多一字下的跟涨套利（300 优先） | 68-76 | 依赖竞价涨幅与盘中分时 |
| 2 | 主线领涨龙头分歧前·后排跟涨（首板+1进2） | 77-85 | 需封单额与分时量价同步 |
| 4 | 分歧转弱·候补股卡位 | 99-107 | 原书成功率 30%~40%，需分时卡位 |
| 5 | 主线题材·分支扩散 | 108-116 | 需人工产业链拆解、无题材树数据 |
| 7 | 冰点反核（高风险博弈） | 130-138 | 需盘中抵抗与历史天量 |
（这五条的 premise/action 文字见 buydian.js 对应行，不是代码阈值。）

### 默认参数（可调，`DEFAULT_RULE`，164-168；`ruleOf` 合并覆盖 170-173）
- p3: `minBoard 3, maxBoard 5, minMarketBoard 5, maxOpenDrop 3, volLo 0.8, sectorAge 7, maxSectorDt 1, minFill 2`
- p6: `minBoard 4, minMarketBoard 5, maxFall 5, volLo 0.8, volHi 1.5, maxSectorDt 1, minFill 2`
- p8: `minWaveBoard 4, minWaveGain 50, minFall 15, maxFall 25, adjLo 3, adjHi 7, minRise 3, volX 1.5, maxSectorDt 2, midMinChg -5`

### 通用前置（181-202）
`bars.length>=35`，当日 K 线存在且 `idx>=30`；需要 `longtou.dayInfo(d)`（题材索引）；`sector = longtou.sectorOf(code)||'—'`；`sec` = 当日该行业条目。条件按顺序短路，第一个不满足即返回 `hit:false`。

### 买点3 分歧日（204-236）
选：前一交易日已 3~5 连板的龙头，次日（信号日）未涨停的良性分歧。条件依次：
1. `prevBoard`（`longtou.boardOf(前一交易日, code)`）`>= minBoard(3)` 且 `<= maxBoard(5)`（209）。
2. 同行业"除自己外最高板" `rivals.second === 0 || prevBoard > rivals.second`（212；`secInfoList` 323-328 取 **信号日当天** `info.items` 的板数，与"前一日板数"比较，混用两天口径）。
3. `b.changePct < 9.8` 且 `> -9.8`（214-215）。
4. 开盘 `openPct = (open/prevClose-1)*100 >= -maxOpenDrop(3)`（217-218）。
5. `volume/prev.volume >= volLo(0.8)`（220-221）。
6. `info.maxBoard >= minMarketBoard(5)`（223）。**【注释≠代码】** POINTS 文案写"全市场最高板 > 5 板"(94)，代码是 `>= 5`。
7. `sectorAge(d, sector) >= sectorAge(7)`（225；`sectorAge` 372-387：看信号日之前 `max(0,di-9)..di-1` 共至多 **9** 个交易日中该行业 `zt>0` 的天数；注释 380 说"7 天窗口"**【注释≠代码】**）。
8. `sec.dt <= maxSectorDt(1)`，`sec.fill1 >= minFill(2)`（227-228；`fill1` = 该行业当日首板家数，来自 longtou.js:340）。

输出：入场 `buy=当日收盘`；`stop = close*0.97`（232）；`ma10`（233）；`target = round(close*1.15)`（234）。均线止损（跌破 10 日线）不在 check 内，在 `backtestPoint` 里（见下）。

### 买点6 首阴（238-268）
选：前日 ≥4 连板龙头，信号日为"上涨以来第一根阴线"。条件依次：
1. `prevBoard >= 4`（242）；2. 同买点3 的 `rivals` 领先条件（244）；
3. `close < open` 且 `changePct < 0`（246-247）；
4. `isFirstYin(bars,i)`（248；331-344：从 `i-1` 向前至多 15 根找最近一根 `changePct>=9.8` 的 K，记为 start；若 15 根内没有涨停则 start=i-1；要求 start+1..i-1 之间无 `close<open`）；
5. `changePct >= -maxFall(5)`（250）；6. `volume/prev.volume ∈ [0.8, 1.5]`（252-253）；
7. `close >= MA5`（255-256）；8. `info.maxBoard >= 5`（258；**【注释≠代码】** 文案写 ">5"(126)）；9. `sec.dt <= 1`、`sec.fill1 >= 2`（259-260）。
输出：`buy=close`，`stop=close*0.97`，`ma10`，`target=close*1.15`（262-266）。

### 买点8 二波（270-311）
选：40 日内有 ≥4 连板且第一波涨幅 ≥50% 的"明牌龙头"，调整 3~7 天后的放量首阳。条件依次：
1. `findWave(bars,i,40)`（347-368）：窗口 `[i-40, i]`（含信号日）内统计连续 `changePct>=9.8` 的最长连板数 `maxBoard`；`peakIdx` = 窗口内最高收盘价那根；`waveStart` 每遇到一次新连板起点就被覆盖（355），即取 **最后一段** 连板起点前一根；`gain = peakClose/base-1`。要求 `wave.maxBoard >= 4`、`gain >= 50`（275-276）。注意 peak 取整个窗口最高收盘，不限于那一波内。
2. `i - peakIdx ∈ [3, 7]`（279-280）；
3. `fall = (1-close/peakClose)*100 ∈ [15, 25]`（282-283）；
4. `close >= MA30`（285-286）；
5. `changePct >= 3` 且 `close > open`（288-289）；
6. `volume / 前5日均量(bars[i-5..i-1]) >= 1.5`（290-292）；7. `close >= MA5`（293-294）；
8. 调整末期缩量：`mean(volume of i-2,i-1) < volume@peak`（296-298，峰值日成交量为基准；峰值量<=0 则直接通过）；
9. `sec.dt <= 2`（300）；`sec.midChg === null || >= -5`（301；`!sec` 也放行）。
输出：`buy=close`，`stop=close*0.97`，`ma5`，`target=close*1.15`，`target2=close*1.25`，`wave{maxBoard,gain,peak,fall}`（303-309）。**【注释≠代码】** POINTS 文案写"止损：买入价−3% 或跌破 5 日线 / 止盈 +15%/+25% 两档"(149)；回测只用 `targetPct`(默认15)，`target2` 不参与回测(见下)。

### scan(date, point)（399-462）
候选池：当日 `info.items` + 前 3 个交易日 `ztHistory.itemsOn(dd,3)`；`point===8` 时再加前 12 个交易日 `itemsOn(dd,4)`（418-424）。逐只 `ztHistory.klineOf(code,121)`，并发默认 4；`bars<35` 跳过。结果按 `wave.gain` 降序。

### backtestPoint（471-617，仅买点 3/6/8 有意义）
- 默认：`days 60`（夹在 5~250）、`maxHold 5`、`stopPct -3`、`targetPct 15`、`costPct 0.2`、`frac 0.25`（472-478）。
- 候选池：区间内每个信号日的 `itemsOn(d, point===8?4:3)`（494-498）。K 线取 `days+maxHold+60` 根（502）。
- 入场：信号日**次日开盘价**买入（533-544，T+1，注释"信号收盘才确认"）。
- 出场（从入场次日起至多 maxHold 根）：① `low <= entry*(1+stopPct/100)` → 价 `min(slPx, open)`，"止损"（557-560）；② 均线止损 `close < MA(maN)`，maN 买点8=5、其余=10，价=收盘（549,562-565）；③ `high >= entry*(1+targetPct/100)` → 价=止盈价（567-570）；④ 第 maxHold 根收盘"到期平仓"（571-573）。同日先判止损（悲观）。
- 成本：`net = gross - costPct*2`（579）→ 默认**往返 0.4%**。**【与 backtest.js/playbook.js 不一致】** 见下文：它们是 `-costPct` 一次。
- 输出统计走 `backtest.statsOf(trades, frac)` / `groupBy`（598-600）；`caveat` 自述数字偏乐观（613-615）。
- 回测里买点8 的入场 stop 用 `stopPct`(-3% 相对**次日开盘价**)，而非 check() 里的 `close*0.97`。

### 需要的输入数据
- 涨停历史库（`ztHistory.load/itemsOn`：每日涨停名单含板数 `b`、`ztItems`），回溯至少"前一交易日 + 题材发酵 9 日"，scan 候选再回溯 3/12 日；回测 days+maxHold+60 根日K。
- 个股日K（open/close/high/low/volume/changePct，至少 35 根，买点8 看 40 日 + MA30）。
- 行业映射 + 题材索引（longtou.js：`sectorOf`、`dayInfo`、`allDates`）。
- 文件自述的近似：换手率 15%~25% 用"成交量/前一日 0.8~1.5"代替；封单/龙虎榜不判；"中军"=行业内区间累计成交额最大者；"题材"=东财行业；良性分歧/首阴退化为"收盘守住均线+量能"，口径偏乐观（buydian.js:13-20）。
- 未实现：买点 1/2/4/5/7。

---

## longtou.js（三龙识别、题材梯队、行业映射、题材索引）

### L1 行业映射 `refreshIndustry`（94-138）
- 来源：东财 `push2 clist`，`fs = m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23`，字段 `f12(代码) f14(名称) f100(行业) f26(上市日期)`，每页 100，并发 3（62-64,166-178）。无行业记为 `'—'`（107）。落盘 `longtou/industry-map.json`；新抓不到的沿用旧值（125-131）。
- `sectorOf(code)` 只读本地缓存，不发请求（83-88）。**行业 ≠ 题材**，文件自述用东财行业近似题材（17-18）。

### L2 新股/次新 `newStockOf(code, asOf)`（152-164）
`days = floor((asOf或现在 - 上市日)/86400000)`（**自然日**，非交易日）；`≤15 → '新股'`，`≤90 → '次新'`，`≤365 → '次新+'`，否则 `'老股'`。无上市日返回 null。（被 guard.js 使用。）

### L3 题材索引 `buildIndex`（203-314）
按 日期×行业 聚合，落盘 `longtou/sector-index.json`：
- `zt`：`changePct >= 9.8`；`dt`：`<= -9.8`；`up/down` 按 `>0 / <0`（254-272）。
- "中军"= 行业内**全部本地 K 线累计成交额**最大的一只（`bars.length>=20` 的股票才参与，222-247），常量，不随日期变化；`midChg` = 该中军当日 `changePct`（271）。
- 行业内连板分布 `board{}`、`maxBoard` 取自 zt-history 的 `ztItems`（277-291）。
- 精简：`amount` 合成亿元；`codes` 只保留前 30 个涨停代码（293-301）。**【注释≠代码】** 293 行注释写"codes 只留板数≥2 的高标"，代码未按板数过滤。
- 因 `sec(b.date,s).midChg=...` 对每个行业中军每天都调用，行业条目每天都存在（影响"零涨停"判断时只看 `zt>0`，无影响）。

### L4 `dayInfo(date)`（321-357）
返回 `zt/dt/up/down`、`maxBoard`（取 zt-history 的 `ddb.maxBoard`，缺则用最大行业 maxBoard）、`premium/advance`（来自 zt-history）、`sectors[]`（`fill1 = board[1]` 首板家数，按 `zt` 降序再 `maxBoard` 降序）、`items = ddb.ztItems`。`boardOf(date, code)`（374-381）= 该日 `ztItems` 里的板数，未涨停为 0。

### 三龙识别 `sanlong(date, {quietDays=5, highBoard=5})`（388-529，默认值 389-390）
**高度龙 gaodu**（408-423）：当日 `ztItems` 中 `b === today.maxBoard && b>0`，按成交额 `a` 降序；输出 code/name/board/sector/amount/price/yizi。
**先于龙 xianyu**（424-456）：对当日每个行业 s：
- `s.zt >= 2`（单个涨停不算，429）；
- 过去 `quietDays(5)` 个交易日（`dates[max(0,di-5)..di-1]`）该行业 `zt > 0` 全为否（431-438；某天索引缺失视为静默）；
- 取该行业当日 `ztItems` 中板数最高（并列取成交额最大）者作为先于龙；
- 结果按 `sectorZt` 降序、`board` 降序，取前 8（456,521）。
**补涨龙 buzhang**（458-495）：
- 取 `gaodu[0]`（成交额最大的高度龙）；若 `board >= highBoard(5)`，同行业其它涨停股中 `b ∈ [1,2]` 全部入选，`gap = gTop.board - b`，按 `gap` 降序再成交额降序（462-476）。
- 其它行业若 `s.maxBoard >= highBoard` 且不在已选行业内，取该行业 `b∈[1,2]` 中 gap 最大者 1 只（479-495）。总数截前 12（522）。
**退潮 recession**（497-509）：前一日 `maxBoard` 最大的行业 prevTop；`fading = 今日该行业 zt < 前日该行业 zt`。
输出：`gaodu/xianyu/buzhang/recession/market/note`。无入场/止损/止盈。

### 题材梯队 `ladder(date, sector)`（541-573）
行业未找到则用 `sectors[0]`。成员 = 当日该行业 `ztItems`，按板数降序再成交额降序；`role`：`b === s.maxBoard → '龙头'`，`b>=2 → '中位'`，否则 `'补涨'`（556）；`complete = rows>=3 && 补涨>=2`（569）。注：若行业 maxBoard=1，全部成员都标'龙头'（无'补涨'）。`share` = 成员成交额占比。自述"带动性"只能用同题材涨停家数近似（559）。

### 个股K线强度 `klineScore(bars, n=20)`（583-649）
窗口最近 n 根，统计 `zt(>=9.8)`、`bigUp(>=5)`、`up/down`、`bodyWin(close>open)`、`upRatio = up/(up+down)*100`。档位与基础分：
`zt>=2 → 涨停基因 90+min(10,2zt)`；`zt===1 → 有涨停 78`；`bigUp>=3 && upRatio>=55 → 大阳小阴 68`；`upRatio>=55 && bodyWin>=round(n*0.5) → 阳多阴少 56`；`upRatio>=50 → 阴阳均衡 44`；否则 `阴盛阳衰 22`。再按涨跌幅总量比 `strength = sumUp/sumDown` 微调：`>=2 → +6`，`>=1.2 → +3`，`<0.8 → -6`，夹在 0~100（612-636）。

### 均线共振 `maAlign` / `resonance`（652-728）
- `maAlign.bull = MA5>MA10>MA20`（需≥20 根，664-669）。
- `resonance(date,sector,limit=20)`：样本 = 该行业当日涨停股 + 行业其余成分股（来自行业映射），前 `limit(20)` 只；每只取 90 根日K、截断到 date、需 ≥25 根；输出 `bullRatio` 和 `avgScore`（677-712）。文案"≥70% 合力、<40% 涣散"只在 note 字符串中（724-726），代码不据此分级。

### 需要的输入数据
- 本地日K全市场（`ztHistory.universe()/readKline`，buildIndex 要 ≥20 根）；涨停历史库（`ztItems` 含 `c,n,b,a,p,y`）；东财 clist（行业、上市日期，一次性）；`resonance` 用 `ztHistory.klineOf(c,90)`。
- 文件自述的近似/局限：涨停池 push2ex 只保留约 15 天，历史靠 zt-history 反推；zt-history 无行业字段，需另建映射；行业近似题材；中军用累计成交额近似"大市值核心"；"涨幅最大"用成交额并列裁决（409）。
- 涨停判定 `>=9.8` 对 20cm/ST/北交所不做区分（由 changePct 数值决定，代码无板块分支）。

---

## jiedian.js（出手节点）

自述实现一半：大盘指数类 4 个 + 情绪连板类 5 个；**不做** 事件驱动类、个股趋势类（形态）、权重补跌原义（用上证50 指数代替）（jiedian.js:2-25）。

### 数据：`indexKline(code)`（53-89）
腾讯 `fqkline`，`day,,,320,qfq` 最近 320 根；缓存 `longtou/idx-<code>.json`，6 小时内直接用；失败回退旧缓存。用到 `sh000001`（上证）、`sh000016`（上证50）。`idxAt(bars,date)` 找不到该日则回退最近的前一交易日（93-99）。`avgVol(bars,i,n)` 窗口含第 i 根本身（108-113）。

### N1 多连冰反转节点 `duolianbing(date, opts)`（127-229）
5 个信号**各自独立判定后计数**（并无"递进闭环"的顺序门控）；`level`：`pass>=4 → 高`，`=3 → 中`，`=2 → 低`，否则 `未构成`（218）。i<6 报"指数数据不足"。
1. 加速大阴线/跳空：`chg = close/prevClose-1 < -1.5%` **或** `gap = open/prevLow-1 < -1%`（143-144）。**注意**：跳空是对昨日**最低价**算的，而注释写"跳空低开幅度 < −1%"（121）。
2. 连冰放量：`iceDays`（从当日向前连续 `close<昨close` 的天数）`>=3`，且当日 `volume > 5日均量*1.2`，且前一日 `volume > 其 5日均量*1.2`（147-161）。
3. 长下影：`body>0 && 下影 > 实体*2`（下影 = `min(open,close)-low`，仅当 low 低于 open 和 close，164-167）；"最低点出现在上午"无法验证，退化（注释 123）。
4. 跌停减少：`dtPeak`（`dates[di-5..di]` 内跌停家数最大值）`> opts.dtPeak(默认30)`，且次日跌停数 `<= dtPeak*0.5`（171-188）。次日数据不存在则不通过。跌停家数来自 `dtSeries()`（361-371，由 longtou 索引按本地 K 线 `<=-9.8` 统计，进程内永久缓存）。
5. 上证50 近似权重补跌：`c50 < opts.w50Drop(默认-2.5)` 且（次日不存在 **或** 次日最低 `>=` 当日最低）（194-201）。次日未到时仅凭当日跌幅就通过。
- `opts.window`（默认 8，133）算出 `from/seg` 后**从未使用**（134-135），属死参数。
- `advice` 文案：`pass>=4` 可考虑右侧跟进；3 继续等；其余"不构成"（221-226）。无入场价/止损。

### N2 极限地量 `didiliang(date)`（237-290）
i<25 报错。`isDiliang = (volume/20日均量 < 0.6) && (volume <= 近20根最小量)`（246-251，窗口含当日）。"发令枪"`gun`：从 `i-1` 向前至多到 `i-12`（**【注释≠代码】** 注释 253 写"往前 10 天"），找最近一次地量日 k（同样条件），再看 `k+1..min(len-1,k+5)` 中首个 `volume > 地量日量*1.5 且 close>open` 的日子（264-269；**【注释≠代码】** 注释 235 写"地量后第 3~5 天"，代码从 k+1 开始）。该循环不限定在 date 之前，历史日期会读到之后的数据（前视）。输出 `isDiliang/volRatio/lastLowAt/gun/advice`。

### N3 跳空缺口加速 `gapAccel(date)`（296-321）
`hit = open < prevLow && chg < -1 && volume/5日均量 > 1.2`（302-307）。**【注释≠代码】** 注释 293-294 写"当日跌幅 > −1%"，代码为 `chg < -1`（跌超 1%）。文案标明是高风险左侧博弈。

### N4 关键位破位 `breakMa250(date)`（327-354）
需 ≥250 根；`below = close < MA250`，`dist=(close/MA250-1)*100`，`recovered = 次日 close >= MA250`（无次日为 null）。只给位置，不判护盘（352）。无 hit 阈值。

### N5 情绪连板类 `emotionNodes(date)`（376-498），全部在本地涨停库/题材索引上计算
- **冰点 ice**（385-394）：近 20 日（`dates[di-19..di]`）涨停家数升序，取第 `floor(len*0.2)` 个为 p20；`isIce = info.zt <= p20 && info.maxBoard <= 3`（原书无死阈值，注释 386）。
- **冰点反核 fanhe**（396-414）：`k ∈ [max(1,di-3), di-1]` 中任一天满足同样冰点规则（各自用其前 20 日分位），且 `info.zt > prev.zt`。
- **高低切换 switch**（416-420,457-462）：`sanlong(date)`（默认 quietDays=5, highBoard=5）里 `xianyu.length>0` 且 `recession.fading`。
- **补涨龙确认 buzhang**（463-468）：`sanlong.buzhang.length>0`。
- **弱转强 ruo**（422-442,469-474）：遍历**前一日涨停名单** `prev.items`，取该票前日与当日K（需 ≥30 根），`yin = yest.close<yest.open || yest.changePct<9.8`，且 `today.close>today.open && today.changePct>0` 计 1 只；节点 `hit = 计数 >= 2`。**读代码的推断**：因遍历的就是昨日涨停名单，`yest.changePct<9.8` 分支一般不成立（除非 ztItems 与 9.8 口径有出入），实际主要靠"昨日收阴"。
- `advice`：`isIce&&fanhe`→反核窗口；`isIce`→等反核；`maxBoard>=6`→情绪偏高；否则中性（489-496）。
- 输出另含 `market{zt,dt,maxBoard,premium,advance,zbRate}`（zbRate 取 zt-history `dayDb.zbRate`）、`ruoList`（≤8 只）。

### 需要的输入数据
- 指数日K：`sh000001`（≥250 根用于 MA250）、`sh000016`（上证50）；腾讯 320 根。
- 涨停历史库/题材索引（跌停家数序列、涨停家数、maxBoard、昨日涨停名单、zbRate）；个股日K（弱转强，≥30 根）。
- 自述无法还原：③"最低点出现在上午"（只看影线比）、⑤"前10大权重股加权跌幅"（用上证50 指数）、地量"早盘30分钟成交额超过地量日全天"（用全天量>1.5倍）、"护盘力量启动"（不判）。

---

## cycle.js（情绪周期）

数据：`dayStat(date)`（29-54）用 **东财涨停池/炸板池** `em.fetchLimitUpPool/fetchBrokenPool`（实时接口，非本地库）：`total`、`maxBoard=max(boardTimes)`、`lianban = 板数>=2 家数`、`erban`、`sanbanPlus`、`zb`、`brokenRate = zb/(total+zb)*100`（保留1位）。`series(end, days=5)` 取最近 days 个交易日（`limitupTable.calendar()`）。无任何用户可配阈值，全部写死。

### 判定顺序 `judge(stats, extra)`（168-283）
共 **7 阶段**（STAGES 89-157：freeze 冰点、chaos 混沌、start 启动、ferment 发酵、main 主升、climax 高潮、ebb 退潮），按下列顺序取第一个命中：
指标：`lowBoard = mb<=3`、`fewLianban = lb<=10`、`thinPool = tt<=30`（186-188）；`premium = extra.prevZtAvgOpen`（昨涨停今竞价均值，可缺）。
1. **退潮 ebb**（203-208）：三票取二 `[mbDrop>=1, brokenRate>=35, premium<0]` 且 `mb>=3`。`mbDrop = prev.maxBoard - mb`（无 prev 为 0）。
2. **冰点 freeze**（210-217）：`[lowBoard, fewLianban, thinPool]` 命中 ≥2。**【注释≠代码】** 头注/注释写"连板为个位数、首板只剩 20 只左右"（8、210），代码是 `lb<=10`、`total<=30`（涨停总数而非首板数）。
3. **高潮 climax**（219-223）：`mb>=7 && tt>=80`。
4. **主升 main**（225-229）：`mb>=5 && lb>=15`。
5. **发酵 ferment**（231-235）：`mb>=4 && lb>=8 && (br==null || br<30)`。
6. **启动 start**（237-248）：`mb>=3 && (brUp<0 || premium>0 || mb>prev.maxBoard)`，其中 `brUp = br - prev.brokenRate`。
7. **混沌 chaos**（250-254）：兜底。
每阶段带 `action/position` 文案（如冰点"空仓或极轻仓（≤1成）"、启动"中仓3~5成"、发酵"重仓5~8成"、主升"7~10成"、高潮"减仓"、退潮"空仓"），非计算值。
情绪温度 `score`（256-261）：`sBoard=min(100,mb/7*100)`、`sLianban=min(100,lb/25*100)`、`sBroken = br==null?50:max(0,100-br*2)`、`sPremium = premium==null?50:clamp(premium*12+50,0,100)`；`score = round(0.3*sBoard+0.3*sLianban+0.2*sBroken+0.2*sPremium)`。
`analyze(date, opts)`（291-304）：`days` 默认 5。
- JSDoc 提到 `prevZtWeak/prevZtCount`（166,289）但代码未使用。
- 无入场/止损规则；输出 `stage/score/reason/signals[]/delta/trend`。

### 需要的输入数据
东财涨停池 + 炸板池各 N 日（默认 5，东财历史仅十余天）；交易日历；可选 `prevZtAvgOpen`（昨涨停今竞价均值，由调用方提供，本文件不计算）。文件自述规则来自 "D:\大A" 资料文字拆解（cycle.js:4-6）。

---

## playbook.js（低吸×2、做T×2、趋势×2）

通用：`ma/mv/atr(14)`（57-85；atr 要求 `end+1>=15`）。`METHODS` 元数据 91-176（界面卡片），`backtestable` 对做T=false。成本默认 0.2%（单次扣，566,632）。

### P1 dixiMa 缩量回踩 MA10（META 92-105；扫描 245-253；回测 503-511）
- 选：近若干日涨停股回踩。扫描：`fall=(现价/涨停日收盘-1)*100 ∈ [-12,-1]`；`现价 >= 涨停日最低*0.98`；`现价 >= MA10*0.97`；`!(现价 < MA20*0.92)`；`volR5 <= 1.0`（最近已收盘一根的量 / 其5日均量，null 放行）。
- 候选池：`recentLimitUps(6)`（205，**【注释≠代码】** META 写"近 5 日内有涨停"(99)，扫描取库中最近 6 个日期键；回测要求 `daysSince ∈ [1,5]`，508-509）。每只取库中**最高板**的那次涨停（`b>cur.board` 严格大于，早的优先，191-194），取前 `MAX_FETCH=60` 只且需在当日快照里（213）。
- 输出：`buyLow=MA10`、`buyHigh=MA5`、`stop=startLow*0.97`（302）；评分 `0.35*max(0,100-|fall|*5)+0.35*volScore+0.3*min(100,40+(board-1)*20)`（274-279）。
- META 声明：止损"涨停日最低价下方 3%"；目标"+8% 或 5 日内高点"；回测配置 `stopPct -3, targetPct 8, maxHold 5`（104）。

### P2 dixiN N 字回踩（META 106-119；扫描 255-270；回测 512-524）
- 扫描：该票库内最高板 `=== 1`；涨停后最多 3 根 `after`；前 2 根 `low >= startLow*0.98` 且各自 `volume <= 含自身的5日均量`（`mv(bars.slice(0,i+1),5,i)`，均量含当根自身，262-264）；最近已收盘一根 `close>open`；`lastBar.date >= after 末根日期`（266）。**注意**：`after` 只有 1 根时 `slice(0,2).every` 仍通过，扫描并不严格要求"2 日"。
- 回测 `signalAt`：`info.board===1`、`daysSince === 3`、`b[ztIdx+1], b[ztIdx+2]` 的 `low >= ztBar.low*0.98` 且各自缩量、信号日 `close>open`（513-523）。
- META：止损启动位下 3%，目标"+10%"，`bt {stopPct -3, targetPct 10, maxHold 6}`（115-118）。

### P3 tBias 分时均线乖离做T（META 120-133；`scanT` 401-466）
- 判定：读当日分时（`quote.fetchMinute`，≥20 个点）；`avg = 末点 avg（>0）或 价格均值`；`bias=(last-avg)/avg*100`；`bias<=-2 → 正T低吸`，`bias>=2 → 反T高抛`，否则观望（430-433）。
- 输出 `sellZone=avg*1.015`、`buyZone=avg*0.985`、振幅 `amp`。目标池：默认自选（`config.DEFAULT_WATCHLIST`）里在快照内的票，没有就取快照前 12 只，最多 12 只、并发 3。
- META 里的 "日内差价≥2.5% 落袋、亏损 1.5% 止损、大盘暴跌停做T" **只是文案**，代码不计算。不回测。

### P4 tRoll 半仓滚动做T（META 134-147）
- 与 P3 共用 `scanT`（`methods:['tBias','tRoll']`，441）；代码仅有 `±1.5%` 价位。META 的 "或当日次高/次低点、振幅<1.5% 不做T、收盘T仓归零" **代码未实现**。不回测。

### P5 trendMa 多头排列回踩 MA5（META 148-161；扫描 346-349；回测 531-538）
- 条件：`MA5>MA10>MA20`、`MA5 > 3根前的MA5`、`价/收盘 > MA20`、`bias5=(价-MA5)/MA5*100 ∈ [-2, 2]`。扫描池 = 快照中 `price>0` 的前 120 只（未排序，323），日K ≥60 根。
- META 声明 `bt {stopPct -5, targetPct 15, maxHold 15, trailAtr 2}`（160）。

### P6 trendAtr ATR 通道（META 162-175；扫描 351-354；回测 539-545）
- 扫描：`价>MA20 && MA5>MA20 && 价-MA20 <= ATR14`（未检查 ATR%）。
- 回测：`close>MA20 && MA5>MA20`，`close-MA20 <= ATR14`，且 `ATR% = a14/close*100 ∈ [1.5, 8]`（544）。**【扫描≠回测】** 扫描没有 ATR% 过滤，也没有"首次回踩"检测（META 写"首次回踩"）。
- 扫描输出 `trail = max(价-2*ATR14, MA20)`（358）。`bt {stopPct -6, targetPct 30, maxHold 20, trailAtr 2}`（174）。

### 回测引擎 `backtest(opts)`（554-663）
- 区间：`days` 默认 60（20~200）；信号日从 `max(30, endIdx-days+1)` 到 `endIdx-maxHold`（574-577）。候选 = `recentLimitUps(days+10)` 里的股票，最多 400 只，日K `days+maxHold+90`（580-589）。**候选只来自区间内有涨停的票**，趋势类也如此（注释 579）。
- 入场：信号日后第一个有K线日的**开盘价**（603-610）。
- 出场（从入场次日起）：`low <= entry*(1+stopPct/100)` → `min(stopPx, open)`"止损"；若 `trailAtr` 为真：`trail = bar.close - trailAtr*ATR14; bar.close < trail && bar.close < entry` → "移动止损"；若 `!trailAtr && high>=tgtPx` → 止盈价；第 `maxHold` 根收盘"到期"（616-630）。
- **缺陷（读代码得出）**：移动止损用 `bar.close` 同时算 trail 和判定（624-625），条件等价于 `close < close - 2*ATR14`，ATR>0 时恒为假，**移动止损永远不触发**；趋势两法因 `trailAtr=2` 还会关闭固定止盈（628），实际出场只有固定止损（-5%/-6%）和到期（15/20 日）。
- 候选/选择偏差：`ztMap` 每只票只记窗口内最高板那一次涨停（193），`dixiN` 要求该最高板 `===1`，会按未来信息剔除后来成为多板的票（前视）。
- 成本：`ret = (exit/entry-1)*100 - costPct`（默认 0.2，单次）。统计：胜率、均收益、盈亏因子、最好/最差、`byReason`；交易列表截 60 条。`signalAt` 中 `dixi` 的 `stopRef`(启动位*0.97) 被返回但回测**未使用**，实际止损是相对入场价的 `stopPct`。
- 缺 bar 时 `break`→`exitPx==null`→该笔直接丢弃（631），无提示。

### 需要的输入数据
涨停历史库（近 6 日 / `days+10` 日）、个股日K（扫描 40~90 根，回测 days+maxHold+90）、当日快照（`code/name/price/amount/volRatio/floatCap/auctionPct`）、分时（腾讯，仅做T）、`auction.today()`（剔除当日未走完的K）。自述：做T 日线回测不出真实结果；历史盈利不等于未来（playbook.js:24-28）。

---

## dixi.js（强势股低吸）

函数 `run(rows, opts)`（118-251）。默认：`minBoard 1`、`fallMin 1`、`fallMax 12`（119-121）；`LOOKBACK=5`、`MAX_FETCH=40`、`CONCURRENCY=4`（27-29）。候选：`recentLimitUps(5)`（本地库最近 5 个日期键，每码取最高板；无库则退回东财涨停池，仅十余天，72-112）∩ 当日快照，按板数降序截 40。条件依次（171-187）：
1. `fall=(现价/涨停收盘-1)*100`，要求 `fall <= -fallMin` 且 `>= -fallMax`；
2. `现价 >= startLow(涨停日最低)*0.98`；
3. `MA10` 有效且 `现价 >= MA10*0.97`；`现价 >= MA20*0.92`（MA20 有效时）；
4. `volRatio5 = 最近已收盘一根量/5日均量 <= 1.0`（null 放行）。**【注释≠代码】** 头注 11 写"量能 < 5 日均量 85%"，代码阈值是 1.0；
5. 最近 3 根里 `changePct < -5` 的不能有 ≥2 根（185-187）（playbook 版本没有这条）。
评分（189-200）：`0.25*(100-|fall|*6.7) + 0.25*volScore(130-volRatio5*100，夹30~100) + 0.25*min(100,40+(board-1)*20) + 0.25*clamp((现价/startLow-1)*800,0,100)`。
输出：`buyLow=MA10`、`buyHigh=max(MA5,MA10)`、`stop = min(startLow*0.97, (MA10||startLow)*0.97)`（205）。**【与 playbook 不同】** playbook 的 stop 只是 `startLow*0.97`；本文 reasons 文案仍写"启动位下方 3%"(237)，实际 stop 可能更低。无止盈规则。
数据：本地涨停库（5 日）+ 个股日K 40 根（`bars<25` 跳过）+ 当日快照；剔除当日未走完K。局限自述：企稳确认需分时，日线只是弱信号（16-19）。

---

## fanpao.js（龙头反包）

规则来源自述：GitHub `tel9980/longtou_strategy`，只搬规则不搬业绩（1-20）。函数 `run(rows, {minLianban=3})`（72-183）。
- 日期：`d2 = cal[len-3]`（前天，龙头仍连板），`d1 = cal[len-2]`（昨天，断板）（78-79）。
- 候选：`d2` 东财涨停池中 `boardTimes >= minLianban(3)`，按板数降序取前 40（88-95）。
- 条件：K线 14 根里找得到 d1、d2；断板确认 `k1.changePct < 9.8`（118）；现价 `price > k1.high`（`above = (price/high-1)*100 > 0`，127-128）。
- 动态止盈 `takeProfitOf(bt)`（44-50）：`>=5 → 30`，`4 → 25`，`3 → 20`，其余 `15`。止损固定输出 `-7`（162）。
- 评分（137-143）：`sBoard=min(100,50+(bt-3)*10)`；`sAbove`：`above<=1 → above*40`，`<=5 → 40+(above-1)*10`，否则 `max(20, 80-(above-5)*8)`；`sPost=clamp(50+d1Pct*4)`；`score = 0.45*sBoard+0.30*sAbove+0.25*sPost`。
- 本文件只**选股与展示**止损/止盈；"开板止盈"出场只在 backtest.js 实现。
- 入场用**当日快照现价**做盘中口径判定（自述 17-20），非收盘价。
- 数据：交易日历；d2 的东财涨停池；个股日K 14 根；当日快照。自述无法验证原作者 2507%/年化126% 业绩，且其 2022 年回撤 −39%（11-16）。头注称"创业板/科创板被上游硬过滤"（29），本文件内没有该过滤代码，未验证。

---

## trend.js（趋势跟踪 ATR）

函数 `run(rows, {top=50, capital=100000, riskPct=1})`（77-209）。
- 池：快照中 `price>0` 的行按 `amount` 降序取前 `top(50)`（85-88）。日K 90 根，`bars>=65` 且剔除当日后 `>=62`（96-101）。
- 硬条件（113-119）：`MA20` 与 `ATR14>0` 有效；**现价** `> MA20`；`MA5 > MA20`。其余仅用于评分，**不是过滤**：头注写的"MA20 斜率向上过滤"(10-11) 实际只进评分（`slope20 = (MA20/5日前MA20-1)*100`）。
- `stack`：`MA5>MA10>MA20 → 标准多头排列`；`MA5>MA20 → 短期偏多`；否则 `均线纠缠`（124-129）。
- 评分（133-147）：`0.30*sStack(100/60/30) + 0.25*sSlope(clamp(40+slope20*20,10,100)，缺则40) + 0.15*sPos(距MA60；>40% 给30；否则clamp(100-|x-10|*3,20,100)) + 0.15*sAtr(ATR%<1.5→40；≤5→100；≤8→60；否则25) + 0.15*sVol(clamp(50+(量/20日均量-1)*60,20,100))`。**【注释≠代码】** 注释 142 写"成交额比20日均额"，代码用 `volume`。
- 止损（149-153）：`trailStop = 最近已收盘 close - 2*ATR14`；`stop = min(trailStop, MA20)`；要求 `stop < 现价`，否则剔除。
- 仓位：调用 `position.suggest({capital, entry:现价, stop, atrPct, riskPct, maxSinglePct:25})`（155-162），不传 winRate/payoff，所以只有风险定额 / 波动率 / 单票上限三道。
- 无止盈（移动止损持有）。数据：快照（含成交额）、日K 90 根。ATR 实现 56-71（均值型 TR，非 Wilder 平滑）。

---

## guard.js（今日决策 / 大盘情绪 / 新股次新）

它不是单票风控，而是三块：**大盘情绪总评、今日仓位计划、新股/次新建议**。

### G1 `emotionGrade(score)`（23-30）
`>=80 情绪高潮`、`>=62 情绪回暖`、`>=45 情绪中性`、`>=30 情绪退潮`、否则 `情绪冰点`；非数字 `数据不足`。

### G2 `marketEmotion(d)`（37-82）
起点 `score=50`，累加（括号为区间/夹值）：
- 涨跌家数 `(up/(up+down)-0.5)*70`；
- 涨停 `clamp(zt*0.5, 0, 22)`；
- 跌停 `-clamp(dt*0.9, 0, 24)`；
- 封板率 `(zt/(zt+zb)-0.7)*60`；
- 连板高度 `clamp((maxBoard-3)*4, -10, 12)`；
- 昨日涨停今表现 `clamp(prevZtPerf*3.5, -18, 18)`（有值才加）；
- 指数 `clamp(indexPct*6, -12, 12)`（有值才加）。
最后 `round(clamp(score,0,100))`。

### G3 `todayPlan(ctx)`（89-168）
按分数：`<30 建议空仓（0成，最多1成试错）`；`<45 轻仓试错（1~3成）`；`<62 可参与，分仓做（3~5成）`；其余 `可积极打板（5~8成）`（130-159）。附加提示：`maxBoard>=5`、`<=3` 的文案；`封板率<60%` 加风险提示（163-165）；龙虎榜净卖加风险（126）；三龙/龙虎榜只读 ctx 传入值，不自行计算。

### G4 `newStockAdvice(code,row,asOf)`（176-213）依赖 `longtou.newStockOf`
- `新股`（上市≤15 自然日）："不建议建仓；若碰只用 1 成内、当天进当天出"；
- `次新`（≤90）："可小仓建仓：必须站上 MA5 且放量，缩量阴跌不接；止损 −5%"（只是文案，不计算 MA）；
- `次新+`（≤365）："多头排列可低吸，跌破 MA20 离场"（文案）；
- `老股`：按正常战法；
- 附注：`boardTimes>=2` 提示高位博弈；`changePct>=9.8` 提示次新涨停分歧大。无上市日返回 `known:false`。

### G5 `scanNewStocks(rows, asOf)`（220-242）
遍历快照/涨停池，非老股的列出并排序（新股→次新→次新+，再按涨幅）。

### 需要的输入数据
涨跌家数（breadth）、涨停/跌停/炸板家数、最高板、昨日涨停今表现、上证涨幅、行业映射文件里的上市日期。文件头注称"沿用 strategy.emotionScore 口径并扩展"(13)，但 engine 里同时存在 `strategy.emotionScore`（`state.emotion.score`）与 `guard.marketEmotion`（`state.decision.emotion.score`）两个互不相同的分数（见 engine 节），且 strategy.js 未读，无法核对二者关系。

---

## position.js（仓位）

- `kelly(winRate%, payoff)`（34-41）：`f = (p*b - q)/b`，`f<=0 → 0`。
- `streakFactor(loseStreak)`（44-50）：`>=5 → 0.25`，`>=3 → 0.5`，`>=2 → 0.75`，否则 1。**【注释≠代码】** 头注 20-21 只写 3 笔→50%、5 笔→25%，代码另有 2 笔→75%。
- `suggest(o)`（66-169），默认 `riskPct 1`、`kellyFraction 0.25`、`maxSinglePct 25`、`loseStreak 0`：
  - 无资金/买入价，或 `stop<=0 / stop>=entry` 则返回 `ok:false`（91-98）。
  - ① 风险定额：`shares = floor(capital*riskPct/100 / (entry-stop) / 100)*100`（104-107）。
  - ② 波动率目标：`pctByVol = riskPct / (2*atrPct) * 100`（需传 `atrPct`，112-114）。
  - ③ 凯利：`kelly(winRate,payoff)*kellyFraction`（需传 winRate、payoff>0，118-123）。
  - 取最小：`pct=pctByRisk` → `min(byVol.pct)`（仅当 >0）→ `min(byKelly.pct)`（**仅当 >0**）→ `min(maxSinglePct)` → `×streakFactor`（126-143）；股数 `floor(amount/entry/100+1e-6)*100`（151）。
  - **读代码得出**：凯利为 0（负期望）时 `byKelly.pct>0` 为假，凯利这一道不会把仓位压到 0，负期望策略仍可得到风险定额/波动率仓位。
  - 输出 `shares/amount/pctOfCapital/maxLoss/maxLossPct/caps/text`，不足 100 股提示（166）。
- `fromStats(stats, {kellyFraction=0.25})`（175-203）：吃 `backtest.statsOf` 输出的 `count/winRate/payoff`；`count<30 → suggestedPct=0`（196），否则 `kelly*kellyFraction*100`。
- 数据：`capital`、`entry`、`stop`、`atrPct`（可选）、`winRate/payoff`（可选，来自回测统计）、`loseStreak`（调用方给）。trend.js 用到 suggest，未传 winRate/payoff。

---

## backtest.js（回测引擎，仅龙头反包 + 通用统计）

### 回测对象与约定（头注 10-20）
- **只回测龙头反包**（`runFanpao`，230-444）。其它战法回测在 buydian.js / playbook.js 内部各自实现，仅复用 `statsOf/groupBy`。
- 头注三条承认的近似：① 无分时，日线 high/low 判盘中触价，同日一律**先止损**（悲观）；② 开板止盈用"当日最高摸到涨停、收盘没封住"近似，**次日开盘**卖；③ T+1 硬规则（出场最早次日）。
- 摩擦成本默认 `costPct 0.2%/笔`，注释说明含佣金双边约 0.05%、卖出印花税 0.05%、滑点 0.1%（18-19）；`netPct = gross - costPct`（408）。

### `runFanpao(opts)` 默认值（230-238）
`days 60`（5~250）、`minLianban 3`、`stopLoss -7`、`maxHold 5`、`costPct 0.2`、`frac 0.25`、`openBoard true`。
- 信号日 `D=cal[i]`，`d1=cal[i-1]` 断板日，`d2=cal[i-2]` 连板最后日；信号区间 `max(2,endIdx-days+1) .. endIdx-maxHold`（245-249）。候选 = d2 当日板数 ≥ minLianban 的涨停股（优先本地库 `ztHistory.leadersOn`，无库则东财池）（256-296）。
- 入场条件（335-349）：`k1.changePct < 9.8`（断板）；`D 日最高 > 断板日最高`；成交价 `entry = max(breakHigh, D日开盘)`（高开按开盘价）。止盈价 `entry*(1+takeProfitOf(bt)/100)`，止损价 `entry*(1+stopLoss/100)`。
- 出场循环 `j = i+1 .. i+maxHold`（353-383），顺序：① `openBoard`：前一日（j-1）`high/再前日收盘-1 >= 0.098` 且 `changePct < 9.8` → 当日开盘价"开板卖出"；② `low <= slPx` → **价 = slPx**（不与开盘取小，与 buydian/playbook 的 `min(sl, open)` 不同，跳空低开按止损价成交偏乐观）"止损"；③ `high >= tpPx` → 止盈价"止盈"；④ 第 maxHold 日收盘"到期平仓"。入场当日的盘中止损不检查。
- 缺 bar → `exit` 为空 → `unresolved++`，该笔丢弃（385-388）。

### `statsOf(trades, frac=0.25)`（132-198）
`count、winRate、avgNet、medianNet、avgWin、avgLoss、best、worst、payoff=avgWin/avgLoss、profitFactor、maxDD、maxDDAt、maxLoseStreak、finalEquity、sumNet、curve`。资金曲线按出场日排序逐笔复利 `equity *= 1 + netPct/100*frac`；亏损定义为 `netPct <= 0`。`count=0` 返回占位。`groupBy(list,keyFn)`（201-215）按键分组再 `statsOf`（用默认 frac）。
`reliable = count >= 30`；不足则 `warn` 提示（435-439）。

### 需要的输入数据
交易日历；涨停历史库（`leadersOn(d2,minLianban)`，126 天）或东财涨停池（十余天）；个股日K（`days+maxHold+40` 根，本地库优先，否则 `stock-detail.fetchKline` 并缓存 12 小时）。无分时、无复权说明、无涨跌停可成交性约束（一字板买不到/卖不出未建模）、无停牌处理。

---

## engine.js（行情引擎，编排）

**不含任何买卖规则。** 它是 EventEmitter 轮询器，把数据拉齐后做聚合并向渲染进程推 `snapshot`。

### 编排内容
- 轮询间隔取 `config.POLL`（config.js:72-79）：`quotes 5000ms, pools 20000, boards 30000, breadth 60000, trends 30000, tick 1000`；`pollScale` 恒 1（engine.js:76-79,98-107）。
- 启动 `bootstrap()`（115-134）顺序：quotes → pools → trends → boards → prevZT → breadth，每步间隔 350ms，然后 `recompute()` 并推快照。
- `tickQuotes`（166-192）：指数 + 自选 + 战术/八卖列表个股，腾讯行情；指数为空即抛错。
- `tickPools`（228-245）：东财涨停/跌停/炸板池；全空而一小时内成功过则视为接口异常保留旧值。
- `tickBoards`（277-330）：东财板块（行业40/概念40/弱势20/涨幅40/成交30）失败回退新浪。
- `tickBreadth`（334-343）：东财涨跌家数，失败回退新浪，然后重算情绪与决策。
- `tickTrends`（347-382）：上证/深证/创业板分时，腾讯为主，东财回退。
- `tickPrevZTPerf`（386-409）：从昨天起向前最多 12 个自然日找第一个涨停池非空的日期，取其代码批量报价，`aggregatePrevZT`（411-424）得 `avg/upRatio/limitUpAgain`。仅在 bootstrap/refreshAll 中调用，**无定时器**，`_prevZT` 一旦设定就不再重选日期（跨日常驻不刷新，除非 `refreshAll`）。
- `recompute()`（258-273）：调用 `strategy.buildTactics(ctx)`、`strategy.buildBayi(ctx)`（strategy.js:491、450，**未读**，其内容不在本清单），`computeEmotion()`、`computeDecision()`。
- `computeEmotion()`（428-461）：`strategy.emotionScore({zt,dt,zb,breadth,prevZTPerf})` + `emotionLabel`；`sealRate = zt/(zt+zb)`；`maxBoard`、梯队分布。
- `computeDecision()`（464-489）：调用 `guard.marketEmotion`（用 breadth、涨停/跌停/炸板数、maxBoard、prevZTPerf、上证涨幅——取 `sh000001`，缺则 `sz399001`）、`guard.todayPlan({emotion})`（不传三龙/龙虎榜，注释 482 说明避免每次池刷新打网络）、`guard.scanNewStocks(涨停池+跌停池, date)`。
- `recordEmotion()`（491-516）：每分钟一点的情绪时序，最多 400 点。
- `sessionOf`（26-37）：09:15 前盘前；<09:25 集合竞价；<09:30 竞价撮合；<11:30 早盘；<13:00 午休；<15:00 午盘；其余已收盘；周末休市（不识别节假日）。
- engine 本文件**没有**引用 cycle / longtou / jiedian / buydian / playbook / dixi / fanpao / trend / position / backtest；这些模块由别处调用（调用方文件未读，本次未核实）。

### 需要的输入数据
腾讯指数/个股行情与分时、东财涨停/跌停/炸板池、东财（回退新浪）行业概念板块与涨幅榜/成交榜、东财（回退新浪）涨跌家数。

---

## 不确定/读不出的地方

1. `zt-history.js` 未读：`itemsOn(date,minBoard)` 是否为"板数 ≥ minBoard"、`ztItems` 的涨停判定口径（是否与 9.8 一致）、`leadersOn` 的语义、`klineOf` 返回根数/是否复权、`premium/advance/zbRate` 的计算方式，均无法确认。buydian 候选池、弱转强（jiedian 442）的推断依赖这些。
2. `limitup-table.js` 的 `calendar()` 返回格式（YYYYMMDD 升序）与是否含当日，仅从用法推断。
3. `strategy.js`（`buildTactics/buildBayi/emotionScore/emotionLabel/passFilter`）未读：engine 里的"战术/八卖(bayi)"列表与 `state.emotion.score` 的具体规则不在本清单；guard 头注所称"沿用 strategy.emotionScore"无法核对。
4. 谁调用 cycle/longtou/jiedian/buydian/playbook/dixi/fanpao/trend/position（主进程/IPC 文件）未读，因此各模块的实际触发时机、传入的 `rows`（含 `price/amount/auctionPct/volRatio/floatCap` 的来源）、`prevZtAvgOpen` 的来源均未知。
5. 涨停阈值全文统一 9.8，是否对创业板/科创板（20cm）、ST（5%）、北交所（30%）做区分：本批文件内无板块分支；fanpao/backtest 注释称"创业板/科创板已被上游硬过滤"，上游代码未读，未验证。
6. 东财 `fetchLimitUpPool` 的 `boardTimes/industry` 语义、`em.tradeDate()` 的日期/时区行为未读。
7. `quote.fetchMinute` 的返回字段（`price/avg`）与 `auction.today()` 的格式（用于剔除当日未走完K）仅从用法推断。
8. 弱转强（jiedian.js:436）中 `yest.changePct<9.8` 分支是否实际可触发取决于 zt-history 的 `ztItems` 判定口径（见 1），本文的"几乎不触发"是推断。
9. 回测里的涨停一字板买不到、停牌、复权、成交量约束均未建模（backtest/buydian/playbook 皆是），文件内也未声明复权方式。
