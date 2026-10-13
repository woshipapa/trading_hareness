# C-data：短线来财 6.18.0 数据清单与 n8n 现有能力对照

路径缩写（下文所有 file:line 都用它们）：
- `E/` = `/Users/papa/codebase/n8n/state/research/duanxian_laicai_6.18.0/electron/`（app，只读，未运行、未联网）
- `N/` = `/Users/papa/codebase/n8n/`；`app/` = `N/quant-service/app/`
- userData = Electron `app.getPath('userData')`（具体系统路径 app 内未写死，E/main.js:230-236）

## 数据清单

### 0. 总览：数据源与调度

外部数据源（host + path，均为公开免鉴权 HTTP，无登录态；`ut=` 是写死的前端常量，此处不抄）：

| 源 | host + path | 用途 |
|---|---|---|
| 东财 push2ex | `push2ex.eastmoney.com/getTopicZTPool`、`getTopicDTPool`、`getTopicZBPool` | 涨停/跌停/炸板池 |
| 东财 push2 clist | `{push2,1.push2,2.push2,3.push2,4.push2,5.push2,7.push2,11.push2,push2delay}.eastmoney.com/api/qt/clist/get`；另有 `82.push2.eastmoney.com/api/qt/clist/get` | 板块榜、个股榜、涨跌家数、行业映射 |
| 东财 push2 ulist.np | `push2.eastmoney.com/api/qt/ulist.np/get` | 批量快照、个股实时资金流、竞价兜底 |
| 东财 push2 fflow | `push2.eastmoney.com/api/qt/stock/fflow/kline/get`（分钟）| 板块资金流分钟线 |
| 东财 push2his | `push2his.eastmoney.com/api/qt/stock/kline/get`、`.../stock/fflow/daykline/get`、`.../stock/trends2/get` | 日K、资金流日线、指数分时 |
| 东财数据中心 | `datacenter-web.eastmoney.com/api/data/v1/get`（reportName=`RPT_DAILYBILLBOARD_DETAILSNEW`） | 龙虎榜 |
| 腾讯 | `qt.gtimg.cn/q=`、`web.ifzq.gtimg.cn/appstock/app/minute/query`、`web.ifzq.gtimg.cn/appstock/app/fqkline/get` | 实时行情、分时、日K/交易日历 |
| 新浪 | `vip.stock.finance.sina.com.cn/q/view/newSinaHy.php`、`/q/view/newFLJK.php?param=class`、`/quotes_service/api/json_v2.php/Market_Center.getHQNodeData`、`.../MoneyFlow.ssi_ssfx_flzjtj` | 备用源、全市场代码表 |

调度（E/config.js:72-79 `POLL`，单位 ms：quotes 5000、pools 20000、boards 30000、breadth 60000、trends 30000、tick 1000；E/services/engine.js:99-106 用 `setInterval` 起，乘 `pollScale`）。启动时 `bootstrap` 按 quotes → pools → trends → boards → prevZT → breadth 依次拉，每步间隔 350ms 错峰（E/services/engine.js:113-135）。竞价自动采集另用 `setTimeout` 排程：9:26:20 触发，三轮 9:26:20 / 9:27:20 / 9:31:00（E/main.js:586-618；E/services/auction.js:426-430），跳过非交易日（E/main.js:546-553）。其余数据全部由界面经 IPC 按需触发（见各条）。

限流/失败处理（共用，E/services/net.js）：按 host 最小间隔排队 腾讯 70ms / 新浪 220ms / 东财 260ms / 其它 180ms（:87-97）；连续 6 次失败熔断该 host 45 秒（:69-71）；`firstSuccess` 依次试多源（:177-192）；东财 clist 轮询 9 个分片 host（E/services/eastmoney.js:20-53）。E/services/eastmoney.js:9-10 注释：东财长时间高频访问会触发限流熔断，全部失败时由上层切到新浪。

本地存储总表（全部在 userData 下，未见任何删除/清理逻辑，没有 retention 代码；"保留期"一栏均为"无自动清理"，除非另注明）：

| 路径 | 格式 | 写入处 | 内容 |
|---|---|---|---|
| `prefs.json`（electron-store，name `prefs`） | JSON | E/main.js:75-80,129-147 | 偏好、自选股（`watchlist`）、过滤器、`auctionAuto` 等（非行情数据） |
| `build-stamp.json` | JSON | E/main.js:237,251 | 版本戳（清渲染缓存用） |
| `board-fund.json` | JSON | E/services/board-fund.js:45-58 | 最近一次板块资金快照（含 series/points），覆盖写 |
| `board-universe.json` | JSON | E/services/board-fund.js:79-104 | 板块池 `{industry:[{code,name}], concept:[...]}`，每类最多 70（:75） |
| `auction/universe.json` | JSON `{at,list:[{code,name,tsCode}]}` | E/services/auction.js:73-121 | 全市场代码表，7 天过期（:73） |
| `auction/YYYYMMDD.json` + `.csv` | JSON（payload `{date,capturedAt,rounds,total,meta,rows}`）+ UTF-8 BOM CSV | E/services/auction.js:378-384 | 当日竞价快照，一天一份，不覆盖其它日 |
| `zt-history/kline/{code}.json` | JSON 紧凑数组 `[日期,开,收,高,低,额,涨跌幅,量]` | E/services/zt-history.js:80-112 | 单股日K（默认 121 根），12 小时内复用（:93） |
| `zt-history/db.json` | JSON | E/services/zt-history.js:231 | 由日K推导的逐日涨停/炸板库（`days[date]`，含 `ztItems`） |
| `backtest/pool/{YYYYMMDD}.json` | JSON 数组 | E/services/backtest.js:56-77 | 某日涨停池原样落盘，"历史不变，缓存永久有效"（:55） |
| `backtest/kline/{code}.json` | JSON | E/services/backtest.js:82-110 | 日K兜底缓存（本地库没有时才用），12h 新鲜度 |
| `longtou/industry-map.json` | JSON `{at,count,map,names,list}` | E/services/longtou.js:61,135 | code→行业、名称、上市日期 |
| `longtou/sector-index.json` | JSON | E/services/longtou.js:184,311 | 日期×行业涨停聚合索引（本地扫日K生成，无网络） |
| `longtou/idx-{code}.json`（jiedian 用，目录同 longtou） | JSON `{at,code,bars}` | E/services/jiedian.js:38,53-86 | 指数日线，6 小时内复用 |
| `logs/` | electron-log | E/main.js:1450 | 日志 |

龙虎榜只有进程内 `Map` 缓存，不落盘（E/services/longhu.js:56,171）；涨停异动转表、交易日历只有内存缓存（E/services/limitup-table.js:34,102）。assets/board-codes.json 是随包内置的只读板块代码表（E/services/board-codes.js:14），不在 electron/ 目录内，未读。

---

### D01 涨停池（ZT）
- provider/endpoint：东财 `push2ex.eastmoney.com/getTopicZTPool?dpt=wz.ztzt&Pageindex=0&pagesize=220&sort=fbt:asc&date=YYYYMMDD`（E/services/eastmoney.js:112-124）。
- 用到的字段（`normalizePoolItem`，E/services/eastmoney.js:85-110）：`c` 代码、`m` 市场(1=沪)、`n` 名称、`p`（/1000 才是价格）、`zdp` 涨幅%、`amount` 成交额、`ltsz` 流通市值、`tshare` 总市值、`hs` 换手%、`lbc` 连板数（缺则 `zttj.ct`）、`fbt` 首封时间(HHMMSS)、`lbt` 末封时间、`fund` 封单额、`zbc` 炸板次数、`hybk` 行业、`ztp`（/1000）涨停价、`zttj.days`（N 天 M 板的 N）、`zs`。
- universe：全市场当日涨停股（沪深含创业板/科创/北交，过滤在上层）。
- cadence：engine 每 20s（`POLL.pools`，E/config.js:74；E/services/engine.js:228-241），与跌停池、炸板池并发；另按需：`pool:table`（E/main.js:957）、竞价战法取 T-1 起 3 日（E/services/auction-strategy.js:35-45）、`cycle`/`dixi`/`fanpao`/回测各自按日期取（见"消费者"）。
- 历史深度：只有约 15 天。E/services/zt-history.js:6-8 "只保留最近十来天，实测 2026-09-14 以前全部返回 0 条"；E/services/longtou.js:17 "只保留约 15 天"；E/services/limitup-table.js:6 却称可按 `date` 查历史（两处口径不一致，见"不确定的地方"）。
- 限流/失败：重试 2 次，退避 500/1300/2800ms（E/services/eastmoney.js:118）；`tickPools` 在已有成功数据后突然全空时保留旧快照并报错（E/services/engine.js:231-234）。push2ex 是"独立网关，push2 被限流时仍可用"（E/services/limitup-table.js:5）。
- 本地存储：仅回测路径落盘 `backtest/pool/{date}.json`（E/services/backtest.js:56-77），其余为内存。
- 消费者：engine `recompute` → `buildTactics`/`buildBayi`（E/services/engine.js:228-242；战法与"八阵图"卡片）、情绪分 `computeEmotion`（E/services/engine.js:428）；`cycle.js:33`、`dixi.js:97`、`fanpao.js:84`、`auction-strategy.js:37`、`backtest.js:68`、`limitup-table.js:152`。

### D02 跌停池（DT）
- endpoint：同 D01，`getTopicDTPool`，`sort=fund:asc`（E/services/eastmoney.js:113-117）。字段同 `normalizePoolItem`。
- universe/cadence：全市场；与 D01 同一次 `Promise.all`，20s（E/services/engine.js:230-235）。
- 历史深度：同 D01（同接口同窗口，代码未对 DT 单独说明）。
- 本地存储：无。消费者：情绪分、决策（E/services/engine.js:428-464，`computeEmotion`/`computeDecision`）。

### D03 炸板池（ZB）
- endpoint：`getTopicZBPool`，`sort=fbt:asc`（E/services/eastmoney.js:113-117）。
- cadence：20s（同 D01）；另在 `build`（涨停异动转表）里取"前一交易日炸板池"给关键词打"昨日炸板反包"标（E/services/limitup-table.js:152-158,191）；`cycle.js:34`、`auction-strategy.js:48` 也取。
- 历史深度：同 D01。本地存储：无（回测只落涨停池）。

### D04 昨日涨停今日表现（派生）
- 来源：向前最多 12 天找到第一个有涨停池的交易日（D01 历史接口）+ 腾讯批量行情（D08）算均涨幅/上涨占比/再涨停数（E/services/engine.js:386-420）。
- cadence：仅 `bootstrap` 启动时一次（E/services/engine.js:117,386），之后若 `_prevZT` 已缓存则每次调用只刷腾讯行情；注意它不在 `setInterval` 列表里（E/services/engine.js:99-106），即启动后不再定时刷新。
- 本地存储：无。消费者：情绪分 `emotionScore`（E/services/engine.js:`computeEmotion`）。

### D05 行业/概念板块榜
- 东财：`{shard}.eastmoney.com/api/qt/clist/get?pn=1&pz={size}&fid=f3&fs=m:90+t:2+f:!50（行业）/ m:90+t:3+f:!50（概念）&fields=f2,f3,f4,f5,f6,f8,f12,f14,f104,f105,f128,f136,f140,f62`，`np=1&fltt=2&invt=2`（E/services/eastmoney.js:29-31,132,174-181）。弱势榜是同一接口 `po=0`（:180）；按主力净额榜是 `fid=f62`（:188-197）。
- 字段：`f12` BK 代码、`f14` 名称、`f3` 涨幅%、`f6` 成交额、`f62` 主力净额（元）、`f104/f105` 上涨/下跌家数、`f128` 领涨股、`f140` 领涨股代码、`f136` 领涨幅度（E/services/eastmoney.js:137-166）。
- 新浪备用：行业 `newSinaHy.php`、概念 `newFLJK.php?param=class`，GBK，逗号分隔；字段 板块名/股票数/均价/涨跌幅/成交额(万→×10000)/领涨股；无上涨下跌家数、无净额（E/services/sina.js:21-83）。
- universe：全市场板块；取前 40（弱势 20）。
- cadence：30s（`POLL.boards`，E/services/engine.js:277-332），东财失败自动切新浪并标 `degraded`（:316-317）。
- 限流：E/services/board-fund.js:14 注释"东财榜单接口会按 IP 限流"；E/services/board-codes.js:5-6 "clist 按 IP 限流"。
- 本地存储：榜单本身不落盘；板块代码并入 `board-universe.json`（见 D15）。
- 消费者：`buildTactics`/`buildBayi`（板块相关战法）、`board:fund` 的 universe（E/main.js:931-936）。

### D06 全市场个股榜（涨幅 / 跌幅 / 成交额 / 主力净流入）
- 东财：`clist`，`fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23`（沪深 A 股，含创业板/科创），`fid=f3`（涨幅）/`f6`（额）/`f62`（净流入）；字段 `f2,f3,f4,f5,f6,f8,f12,f14,f15,f16,f17,f18,f20,f21,f62,f100,f184`（E/services/eastmoney.js:133-135,199-202）。
- 新浪备用：`Market_Center.getHQNodeData?node=hs_a&sort=changepercent|amount&num&page`，字段 trade/changepercent/amount/turnoverratio/per/pb/nmc/mktcap（E/services/sina.js:87-135）；`nmc`、`mktcap` 乘 10000（万→元，:105-106）。
- universe：全市场 Top 40 / 20 / 30；cadence 30s（随 D05 一起，E/services/engine.js:289-296）；`inflow` 在 engine 里恒为空（:290,331），`fetchInflowTop` 实际无人调用。
- 本地存储：无（`_rawLists` 仅内存，E/services/engine.js:319-325）。消费者：`buildTactics`、过滤器 `applyListFilter`（E/services/engine.js:243-251）。

### D07 涨跌家数（breadth）
- 东财：对 clist 全市场按涨幅降序，二分查找零轴页，约 7 次请求（页大小 100）（E/services/eastmoney.js:210-258）；新浪同法 `sina-binary-search`，7~9 次（E/services/sina.js:143-198）。
- 返回：`{total,up,down,flat,limitUp:0,limitDown:0,buckets:null,method}`；涨跌停家数这里写死 0，由涨停池另供。
- universe：全市场。cadence：60s（`POLL.breadth`），东财失败切新浪（E/services/engine.js:334-343）。
- 本地存储：无。消费者：情绪分/决策（E/services/engine.js:`computeEmotion`、`computeDecision`）、`guard.marketEmotion`（E/main.js:1300）。

### D08 实时行情（指数、自选股、战法/八阵图选中股、单股抽屉）
- 腾讯 `qt.gtimg.cn/q={sh600173,...}`，GBK，`~` 分隔；每 70 只一批，并发 2（E/services/quote.js:90-109）。
- 用到的字段位置（E/services/quote.js:20-25,35-60）：1 名称、3 现价、4 昨收、5 今开、30 时间、31 涨跌、32 涨跌幅、33 高、34 低、36 量(手)、37 额(万→×1e4 元)、38 换手%、39 PE、43 振幅、44/45 流通/总市值(亿→×1e8)、46 PB、47 涨停价、48 跌停价、49 量比。
- universe：配置的 5 个指数（上证/深证/创业板指/科创50/北证50，E/config.js:8-14）+ 自选股（默认 4 只，E/config.js:17-22）+ 战法/八阵图当前入选股；cadence 5s（E/services/engine.js:166-195）。单股 `stock:quote` 按需（E/main.js:846-856）。
- 失败：指数为空则整轮抛错（E/services/engine.js:178）。本地存储：自选股在 `prefs.json`；行情不落盘。
- 消费者：engine、`factors.js:25`、`playbook.js:33`（取行情）、`fanpao` 等。

### D09 指数分时
- 腾讯 `web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh000001`：每分钟 `时间 价 量 额`，app 自己累计算均价 `avg = Σ额/(Σ量×100)`（E/services/quote.js:124-168）。失败退东财 `push2his.eastmoney.com/api/qt/stock/trends2/get?secid=1.000001&fields2=f51..f58&iscr=0&ndays=1`（E/services/eastmoney.js:262-285）。
- universe：上证 / 深证 / 创业板指 三个（E/services/engine.js:347-352）；cadence 30s。历史深度：当日（东财 `ndays` 参数可大于 1，但 app 取 1）。本地存储：无。

### D10 个股分时
- 腾讯 minute/query（同 D09，`fetchMinute`，E/services/quote.js:171-174）；经 `stock:minute` IPC 按需（E/main.js:836-843，`engine.getMinute` 定义于 E/services/engine.js:588）；`factors.js:277`、`playbook.js:414` 也取。universe：单股。历史：当日。本地存储：无。

### D11 个股日K
- 东财 `push2his.eastmoney.com/api/qt/stock/kline/get?secid={0|1}.{code}&klt=101&fqt=1&lmt={n}&end=20500000&fields2=f51..f61`（`fqt=1` 前复权）：日期,开,收,高,低,量(手),额,振幅,涨跌幅,涨跌额,换手（E/services/stock-detail.js:83-119）。失败退腾讯 `fqkline/get?param=sh600173,day,,,{lmt},qfq`，腾讯只有 开收高低量，额=收×量×100（近似），涨跌幅自算，换手为空（E/services/stock-detail.js:38-81）。
- 单股抽屉：`stock:kline`/`stock:chips` 取 180 根（E/main.js:859-877）。
- 全市场日K本地库：`zt-history.rebuild` 对代码表（D19）里的主板非 ST（`allowed`：只要 60/00 开头，E/services/zt-history.js:49-57）逐只取 121 根并落 `zt-history/kline/{code}.json`，并发 5~6，12 小时内复用（E/services/zt-history.js:87-112,206-236；E/main.js:1153-1170）。注释实测：3052 只 × 121 根 100% 成功（:11-12），本地已有 1994 只（:140）；连续拉 3000 只后 push2his 会开始返空（:137-139）。
- 触发：用户点击 `zthistory:build`（E/main.js:1153），不是定时。universe：沪深主板（排除创业板/科创板/北交所/ST）。
- 消费者：`zt-history`（推导）、`buydian.js:430,507`、`trend.js:96`、`dixi.js:143`、`playbook.js:218,328,588`、`fanpao.js:110`、`backtest.js:82`。

### D12 本地涨停历史库（派生，非外部数据源）
- 由 D11 推导：涨停 = 涨幅 ≥ 9.8%（`LIMIT_PCT`，:30）；炸板 = 最高价/昨收−1 ≥ 9.8% 且收盘未涨停；连板 = 连续涨停计数；一字 = 开=高=低=收且涨停（E/services/zt-history.js:166-200）。
- 输出：`days[date] = {zt,zb,zbRate,maxBoard,boardHist,yizi,amount(亿),prevCount,premium(昨日涨停今日均涨幅%),advance(晋级率%),ztItems:[{c,n,b,p,a(亿),y}]}`（:308-331）。落 `zt-history/db.json`（:231）。
- 历史深度：121 个交易日（日K根数，:206-211）。已知推不出：首次封板时间；所以依赖封板时间的战法（一字秒板、竞价抢筹）不能用这份库回测（E/services/zt-history.js:18-21）。
- 消费者：`leadersOn/itemsOn/emotionSeries`（:362-392）→ `buydian.js:413,420,495`、`playbook.js:186`、`dixi.js:74`、`fanpao.js`、`longtou.js`、`jiedian.js`、`backtest.js`；IPC `zthistory:status/build/emotion`（E/main.js:1137-1175）。

### D13 个股资金流
- 历史日线：`push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?secid&klt=101&lmt=30&fields2=f51..f65`：主力、小、中、大、超大净流入（元）、`mainPct`、收盘、涨跌幅；口径"主力 = 大单 + 超大单"（E/services/stock-detail.js:124-155）。
- 实时：`push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids=&fields=f12,f14,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87`（E/services/stock-detail.js:220-251）；失败退新浪 `MoneyFlow.ssi_ssfx_flzjtj?daima=`（r0 超大/r1 大/r2 中/r3 小，净额 = in − out；新浪超大单常为 0，口径与东财略有差异，标 `source:'sina'`，:175-217）。
- universe：单股，经 `stock:fundflow` 按需（E/main.js:879-924），返回最近 10 日 + 3/5/10 日合计。无定时，无落盘。

### D14 筹码分布（本地计算，非外部数据）
- 东财 cyq 接口"已不可用（返回空）"，改本地"三角形分布 + 换手衰减"模型：取最近 180 根日K（需换手率、高低开收量），96 价格档，输出获利比例、平均成本、70%/90% 区间与集中度（E/services/stock-detail.js:5-9,269-325）。
- 输入是 D11 的东财版日K；腾讯兜底版日K 换手为空，会让衰减失效（E/services/stock-detail.js:36-37,288-290）。经 `stock:chips`（E/main.js:868-877）。无落盘。

### D15 板块主力资金流（分钟曲线 + 日线 + 本地板块池）
- 分钟：`push2.eastmoney.com/api/qt/stock/fflow/kline/get?lmt=0&klt=1&secid=90.BKxxxx&fields2=f51..f63`：时间,主力,小,中,大,超大（元，当日累计）（E/services/board-fund.js:157-185）。30s 内存缓存，跨交易日作废（:37,150-155）。
- 日线替补：`push2his.../fflow/daykline/get?lmt=10&klt=101&secid=90.BK..`（:197-235）。
- 批量快照：`push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids=90.BK..×40&fields=f12,f14,f2,f3,f62,f184`（:320-349）。
- 选板块五级降级（:14-18,416-549）：① 实时资金榜（D05 的 `fid=f62`）→ ② ulist.np 批量查本地池 → ③ 主循环板块池 → ④ 本地池逐条拉分钟线自排（最多 45 个、并发 3、缓存 3 分钟，:373-407）→ ⑤ 日线主力净额排序 → ⑥ 读 `board-fund.json`。默认取净流入前 6 + 净流出前 3，行业/概念各一套（:34）。
- 触发：界面按需 `board:fund`（E/main.js:931-951），非定时。universe：东财 BK 板块（行业为主，概念靠运行时累积）。
- 本地存储：`board-fund.json`（最近快照，:45-58）、`board-universe.json`（板块池 ≤70，:75-104）、内置只读 `assets/board-codes.json`（E/services/board-codes.js:14）。

### D16 交易日历
- 腾讯 `fqkline/get?param=sh000001,day,,,260,qfq` 的日期序列 = 近 260 个真实交易日（E/services/limitup-table.js:22,37-44）；失败回退"工作日近似"（周末排除、节假日无法识别），并强行补到 ≥260/320 天（:46-100）。1 小时内存缓存（:38）。
- 消费者：`isTradingDay`（E/main.js:546-553）、`pool:table`、龙虎榜 `latestBoard`（E/services/longhu.js:211-217）、`cycle.js:60,295`、`dixi.js:91`、`fanpao.js:75`、`buydian.js:482`、`playbook.js:569`、`auction-strategy.js:64`。无落盘。

### D17 龙虎榜
- `datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&pageSize=500&sortColumns=BILLBOARD_NET_AMT&sortTypes=-1&filter=(TRADE_DATE='YYYY-MM-DD')&source=WEB&client=WEB`，Referer `data.eastmoney.com`（E/services/longhu.js:27-72）。
- 列：`TRADE_DATE,SECURITY_CODE,SECURITY_NAME_ABBR,CLOSE_PRICE,CHANGE_RATE,BILLBOARD_NET_AMT,BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,BILLBOARD_DEAL_AMT,ACCUM_AMOUNT,DEAL_AMOUNT_RATIO,EXPLANATION,EXPLAIN,TURNOVERRATE,TRADE_MARKET`（:30-46）；元换算成亿（:49），`EXPLAIN` 文本按正则分席位类型（机构买/卖、拉萨散户、游资，:75-84），`EXPLANATION` 归一为连续三日/换手异常/涨幅偏离/跌幅偏离/振幅/无涨跌幅限制/其他（:95-104）。
- universe：当日全部上榜个股明细。时点：每交易日收盘后约 17:00 起更新，盘中通常为空（E/services/longhu.js:9,208）。
- 触发：`longhu:board`（指定日期或回溯 8 个交易日找最近有数据日，E/main.js:1337-1343）、`longhu:track`（最近 N 个自然日里的指定股，:1345-1352）、`guard:decision` 内部取 `latestBoard`（E/main.js:1322）。
- 历史深度：按 `TRADE_DATE` 过滤，可查任意日期（app 默认回溯 8 个交易日 / 跟踪 10 个自然日）；接口本身的最远日期代码未说明。
- 已知局限（E/services/longhu.js:18-22）：明细不带营业部名称（要另调 `RPT_BILLBOARD_TRADEDETAIL`，没做）；`EXPLAIN` 里的"成功率 XX%"是东财算的。
- 失败：重试 2 次、退避 500/1500ms、超时 20s（:64-69）；`track` 单日失败吞掉（:196-198）。
- 本地存储：进程内 `Map`，不落盘（:56,171,204）。消费者：`guard.todayPlan({emotion,sanlong,lhb})`（E/main.js:1322-1326）。

### D18 集合竞价快照
- 采集：腾讯 `qt.gtimg.cn/q=` 每批 50 只、并发 3（E/services/auction.js:137,201-211,282）。解析位置：1 名称 / 2 代码 / 3 现价 / 4 昨收 / 5 今开 / 6 量(手) / 9-18 买一至买五(价,量) / 19-28 卖一至卖五 / 30 时间 / 32 涨跌% / 33 高 / 34 低 / 37 额(万→元) / 38 换手 / 44-45 流通/总市值(亿→元) / 47-48 涨跌停价 / 49 量比（:140-199）。腾讯批量打到一定量会被掐（注释实测 1500 只只回 636），漏掉的用东财 `ulist.np` 补（每批 40，`fields=f12,f14,f2,f3,f5,f6,f8,f10,f20,f21`），补出来的行没有五档、没有涨停价（按主板 10% 由昨收反推）（:213-312）。
- 时点：集合竞价结果只在 9:25~9:30 窗口可取，之后拿不到当时快照，所以必须盘中多轮采样（:6-10）；默认三轮 9:26:20 / 9:27:20 / 9:31:00，同一代码取"竞价成交额更大"的一份，额相同后到覆盖（:17-18,317-330,426-430）。排程见 E/main.js:586-618；`auction:capture`（手动，一轮）、`auction:auto`（开关，`prefs.auctionAuto`）（E/main.js:1031,1382-1390）。
- universe：D19 全市场代码表，排除创业板 30、科创 68、北交所 4/8/9、ST/退（:62-71）。
- 落盘：`auction/YYYYMMDD.json` + `.csv`（:378-384），一日一份；`list()` 列已落盘日期、`latest()` 取最近一份（:398-416）。
- 历史深度：只有 app 自己开着时采到的日子；没有回补途径（接口当日窗口外就没了，:6-10）。
- 消费者：`auction:analyze` → `factors.enrich/emotion/crossDay`、`auction-strategy.history`（E/main.js:1034-1075）；`auction:rank`（:1126）；`longhu.latestBoard` 借用 `auction.today()`。

### D19 全市场代码表
- 新浪 `Market_Center.getHQNodeData?node=hs_a&sort=symbol&asc=1&num=100&page=1..80`，翻页枚举，每页 sleep 60ms（E/services/auction.js:79-123）；新浪挂了就退"600000-600999 粗扫"（:126-133）。
- 落盘 `auction/universe.json`，7 天内复用（:73,81-89,118-121）。含主板/中小板，排除创业板/科创/北交/ST。注释实测 3052 只（E/services/zt-history.js:59）。

### D20 行业映射 + 上市日期 + 题材索引
- 东财 `82.push2.eastmoney.com/api/qt/clist/get?pn&pz=100&po=0&np=1&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14,f100,f26`（E/services/longtou.js:166-181）：`f100` 所属行业、`f26` 上市日期(YYYYMMDD)。约 5562 只 / 100 = 56 次请求，并发 3，"跑一次就够，之后长期复用"（:93-94）。
- 落 `longtou/industry-map.json`（:61,135）；`buildIndex` 再扫本地日K 生成 `longtou/sector-index.json`（日期×行业的涨停/跌停/板数/成交额/中军，:184-311）。
- 触发：`longtou:build`（E/main.js:1237-1248），非定时。用途：新股/次新判定（≤15 日新股、≤90 日次新、≤365 日次新+，`newStockOf`，E/services/longtou.js:150-162）、"三龙"识别（`longtou:sanlong`）。
- 注释的现实约束：行业≠题材，用东财行业近似题材（E/services/longtou.js:19-22）。

### D21 指数日线
- 腾讯 `fqkline/get?param={code},day,,,320,qfq`（E/services/jiedian.js:49,68）；只保留 `date/open/close/high/low/volume`，≥30 根才落盘 `longtou/idx-{code}.json`；6 小时内复用（:53-86）。用于"节点"判断（`jiedian:market`/`jiedian:emotion`，E/main.js:1279-1292）。默认 `sh000001`。

---

### 消费关系速查（strategy 模块 → 数据）
（`rg "require\('./…'\)" E/services/*.js` 结果，E/services/ 各文件 require 行）
- `engine.js`（E/services/engine.js:16-21）← quote、eastmoney、sina、net；产出 `buildTactics`/`buildBayi`/情绪/决策。
- `auction-strategy.js`（:20-21,37,48,64）← D01、D03、D16、D18。 `cycle.js`（:15-16,33-34）← D01、D03、D16。 `dixi.js`（:21-23,74,97,143）← D01、D16、D12、D11。 `fanpao.js`（:23-25,84,110）← D01、D16、D12、D11。
- `buydian.js`（:23,25,413,430,482）、`trend.js`（:22,96）、`playbook.js`（:31-33,186,218,414）← D12、D11、D16、D10。 `longtou.js`（:23-24）← D12、D20。 `jiedian.js`（:29-30,53）← D12、D21。 `backtest.js`（:24-29,68）← D01、D11、D12、D16。
- `factors.js`（:25,277）← D08、D10。 `guard.js` ← D17（通过 main.js:1322）、D07。
- `board-fund.js`、`longhu.js`、`stock-detail.js` 仅被 main.js 的 IPC 直接调用（board-fund 另被 zt-history/backtest 间接用 `detail`）。

## 与 n8n 现有能力对照

n8n 侧总览：能力目录在 `app/datasources/catalog.py`（每个 `_bind` 行写明来源、状态 LIVE_VERIFIED/DECLARED/DORMANT/RETIRED/UNSUPPORTED、落库表与实现文件）；后台任务契约在 `app/platform/runtime_task_registry.py`；数据产品落表契约在 `app/platform/data_product_registry.py`；证据契约在 `app/platform/evidence_contracts.py`；覆盖事实在 `N/docs/work/FACTS.md`；待办在 `N/docs/work/BACKLOG.md`。`docs/ARCHITECTURE.md:117-124` 说明这三份 platform 文件的职责。

| 编号 | 数据集 | app 来源 | n8n 现状 | n8n 位置或证据 path:line | 缺什么 | 已有待办 |
|---|---|---|---|---|---|---|
| D01 | 涨停池 | 东财 push2ex `getTopicZTPool`（20s；约 15 天窗口） | 已有 | 同一接口：`app/datasources/sources/eastmoney_ztb.py:42`（`getTopicZTPool`，docstring :1-8 写明"只给近期窗口，收盘后每日归档一次"）；归档任务 `app/datasources/collectors/post_close.py:54`（`eastmoney_pools` 15:10）、实现 `:110-122`（落 `raw_market_observations`，capability `limit_pool_limit_up`，`app/datasources/catalog.py:550`）。盘中实时用 Fuyao：`catalog.py:548`（LIVE_VERIFIED）、`app/platform/runtime_task_registry.py:42-49`（`market_event_capture` 60s）。历史涨停榜：`N/docs/work/FACTS.md:F004`（`longhuvip_history`/`limit_performance_history` 2023-09-01 至 2026-10-09 共 748 天，一天不缺）。封单/首封详情：`catalog.py:570-576`（`limits.seal_detail`，longhuvip/xuangubao/eastmoney_ztb） | 东财池 n8n 只在收盘后归档（无 20s 轮询），盘中靠 Fuyao 60s；东财池自身历史从归档任务开始日起，FACTS 未记起始日 | —（F004 说明历史已覆盖） |
| D02 | 跌停池 | 东财 `getTopicDTPool` | 已有 | `eastmoney_ztb.py:47`；`post_close.py:110-122`；`catalog.py:555-556`（fuyao_ths `limit_down_pool` DECLARED，eastmoney_ztb DECLARED） | 跌停池的历史起始日 FACTS 未记；Fuyao 跌停池状态是 DECLARED 不是 LIVE_VERIFIED | — |
| D03 | 炸板池 | 东财 `getTopicZBPool` | 已有 | `eastmoney_ztb.py:46`；`catalog.py:553-554`（fuyao_ths `broken_pool` LIVE_VERIFIED；eastmoney_ztb DECLARED）；`app/platform/data_product_registry.py:96`（`sentiment_cycle_daily`：封板/炸板梯队与前日涨停溢价，hot 层不限期） | 炸板池历史起始日 FACTS 未记 | — |
| D04 | 昨日涨停今日表现 | D01 历史 + 腾讯行情派生，仅启动时算 | 已有 | `eastmoney_ztb.py:43`（`getYesterdayZTPool`）、`catalog.py:578`（`limits.previous_limit_up`）；`data_product_registry.py:96`（"prior-limit premium"）；`app/derived_daily_readings.py:1-10`（五个短线情绪读数，只用日线算） | — | — |
| D05 | 行业/概念板块榜（涨幅、涨跌家数、领涨股） | 东财 clist `m:90+t:2/3`，30s；新浪 newSinaHy/newFLJK 备用 | 部分有 | 东财 clist 在 owner 出口不可用：`catalog.py:40`（"push2 clist 全市场在 owner 出口被断连（2026-09-18）"）、`catalog.py:318`（`eastmoney_free quote.all_a_snapshot` UNSUPPORTED）。板块指数行情：`catalog.py:619-621`（`sector.index_quote`，fuyao 收盘 `ths_index_prices_snapshot`，tdx 880xxx，均 DECLARED）；表 `quant.sector_market_observations`（`app/database.py:1046`）；分钟板块截面：`intraday_board_flow_snapshots`（`database.py:806`，`runtime_task_registry.py:38`）；板块成分 `sector_membership_history`（`data_product_registry.py:93`） | 没找到盘中 30s 的"行业/概念榜 + 上涨/下跌家数 + 领涨股"实时榜；板块行情是收盘快照或分钟资金流截面，分类体系是同花顺/Longhu 的，不是东财 BK 代码 | —（B 系列无对应项） |
| D06 | 全市场个股榜（涨幅/跌幅/成交额/净流入） | 东财 clist 全市场 Top N、新浪 hs_a，30s | 已有 | 榜单可由全 A 快照派生：`catalog.py:312-314`（`quote.all_a_snapshot`，fuyao_ths LIVE_VERIFIED，2 页×5000）；`runtime_task_registry.py:87-91`（`all_a_level1_snapshot`，每 60s，09:15 起）；`N/docs/work/FACTS.md:F014`（连续竞价每次 5,535–5,560 行）；净流入 `catalog.py:644`（`flow.stock_daily`） | n8n 存的是全量快照而不是 Top N 榜；没找到榜单视图的专用模块（未穷举） | — |
| D07 | 涨跌家数 | clist 二分/新浪二分，60s | 已有 | `app/market_flow_features.py:127-131`（`advancers/decliners` → `advancer_ratio`）；分钟雷达 `GET /api/v1/market/radar`（`app/routers/market_radar_reads.py:30`），FACTS F010 记其有数据的交易日；全 A 快照 `runtime_task_registry.py:87-91`；指数成分口径涨跌家数 `catalog.py:461`（`breadth.index_daily`，tdx_public DECLARED，注明不得混入全 A 宽度）；历史可由 `canonical_bars_daily` 现算（F001：768 个交易日，每天约 5,300–5,410 只） | 分钟级雷达序列只有 2026-10-09 午后、10-12、10-13 全天（FACTS F010）；更早的涨跌家数需从日线现算 | — |
| D08 | 实时行情（指数/自选/战法股） | 腾讯 qt.gtimg.cn，5s | 已有 | `app/platform/evidence_contracts.py:50`（`tencent_watch_quote`，decision_eligible=True，仅观察池）；`runtime_task_registry.py:34`（`tencent_order_book`，盘中 3s）；`catalog.py:339`（`quote.watch_snapshot` tencent_free）、`catalog.py:363`（`quote.order_book`）；全 A 另有 `runtime_task_registry.py:87`、`:92`（`tdx_all_a_shadow`） | — | — |
| D09 | 指数分时（上证/深证/创业板指） | 腾讯 minute/query、东财 trends2，30s，仅当日 | 部分有 | 授权接口可取指数和 ETF 分钟线：FACTS F007（2022-09-05 至 2026-09-18 抽查全部返回 241 点/天，指数 `SH000001` 等 + ETF）；取数脚本进行中：`N/docs/work/streams/W007-intraday-v-reversal.md:7`（五个指数文件各约 749 行即齐）；第三值不是成交均价而是等权平均涨跌：FACTS F020；分钟资金/涨跌家数雷达：F010 | 指数分钟线目前是本机文件（`docs/work/probes/2026-10-13-index-minute-fetch.py` 的产物），没有进数据库表（F006：库里没有多年个股分钟线表；`catalog.py:450-452` `bars.index_daily` 为 DORMANT/DECLARED，且那是日线）；`high/low/open` 不可直接用（F020） | W007（stream，非 B 编号）；个股侧 B004、B008 |
| D10 | 个股分时 | 腾讯 minute/query，按需，仅当日 | 部分有 | 观察池分钟：`data_product_registry.py:90`（`intraday_minute_sessions`，hot 60 天）、`runtime_task_registry.py:30`（`minute_profile_capture`）、`catalog.py:422-426`（`bars.minute` longhuvip/tencent_free LIVE_VERIFIED，落 `intraday_minute_sessions`）；全市场分钟快照 `runtime_task_registry.py:87`；历史分钟：FACTS F007（授权接口按股票+日期返回 241 点，抽查 2022-09 至 2026-09 的 6 个股票日） | 没有多年全市场个股分钟线（FACTS F006）；只有观察池的当日/近 60 天分钟；历史分钟尚未回补；`market_bars_minute`（`database.py:565`，`data_product_registry.py:89`）存在但 F006 说库里无多年表 | B004（个股分钟线回补）、B005（逐分钟大单净额，F008 只到 2025-01-02） |
| D11 | 个股日K（前复权，121 根/180 根） | 东财 push2his `fqt=1`；腾讯 fqkline 兜底 | 已有 | `quant.canonical_bars_daily`：FACTS F001（2023-08-15 至 2026-10-12，768 个交易日，每天约 5,300–5,410 只）；`app/database.py:445`；`data_product_registry.py:82`（owner 热 365 天 + 冷层）；日线来源 `catalog.py:391-405`（longhuvip_composite LIVE_VERIFIED；Tushare 已于 2026-10-08 停用，见 `catalog.py:391-395` notes）；复权因子 `catalog.py:469-480` | 存的是未复权日线 + 复权因子，不是前复权；2023 年复权因子只有 48.5%（F002），涨跌停价 2023 年 91.4%（F003）；早于 2023-08-15 没有（app 只要 121 根，不受影响） | B001（往前补一年）、B002（2023 复权因子）、B003（2023 涨跌停价）、B010 |
| D12 | 本地涨停历史库（日K推导，121 天） | 由 D11 推导 | 已有 | 真实涨停榜历史 748 天（FACTS F004），比 app 的 9.8% 日K推导更准且含封板时间：`catalog.py:570`（`limits.seal_detail`）；日线现算：`app/derived_daily_readings.py:1-10`；涨跌停价 `daily_trade_limits`（`data_product_registry.py:84`；FACTS F003：该表只有 2026-09-01 起 25 天，日线行里 2024–2026 年 98.5–99.9% 有涨停价） | 日线推导的炸板仍受 B009 规则缺陷影响（板块规则）；2023 年一字/封板判断依赖 B003 | B003、B009 |
| D13 | 个股资金流（日线 5 档 30 日 + 实时） | 东财 daykline/ulist.np；新浪兜底 | 部分有 | 日线主力净额：`catalog.py:644`（`flow.stock_daily`，longhuvip_composite LIVE_VERIFIED，"全市场主力净额，每日约 5300 只"）；表 `stock_money_flow_daily`（`data_product_registry.py:88`）；盘中观察池资金流：`evidence_contracts.py:74`（`eastmoney_watch_flow`，decision_eligible=False）、`catalog.py:649`；分笔派生 `catalog.py:651` | 四档（超大/大/中/小）净额只到 2026-08-20，之后只有不分档主力净额（`N/docs/work/BACKLOG.md:60-61` B006）；盘中全市场个股资金流没有（只有观察池，且不进决策）；逐分钟大单只回溯到 2025-01-02（F008） | B006、B005 |
| D14 | 筹码分布（本地模型，180 日） | 本地由日K 计算 | 部分有 | 试验台里的"成本读数"：`N/docs/work/plans/tactic-trial-ledger.md:140-143`（120 日、半衰期 20 日，`cost_share_below_close`、`cost_share_0_30_above`，"the corrected proxy of `st-chip-distribution`"），谓词 `:376-377`；授权接口声明：`app/longhu_capability_registry.py:32-33`（`StockChouMaByTimeNew_W5`、`GetStockChouMa_New` 列为 RESEARCH_ACTIONS）；Tushare `cyq_chips`/`cyq_perf` 曾验证（`app/database.py:1673-1674`）但 Tushare 已停用（`catalog.py:391-395` notes） | 没有按股票每日落库的筹码分布序列；试验台的成本读数是代理（模型和 app 的三角形分布+换手衰减不同），实现位置在试验台代码中，`rg cost_share` 在 `quant-service/app` 下无命中；Longhu 筹码接口只是研究动作，未接入落库 | B001（120 日成本读数受日线起点限制） |
| D15 | 板块主力资金流（分钟曲线 + 日线，行业/概念） | 东财 fflow/kline klt=1、daykline、ulist.np；本地板块池 | 部分有 | `app/platform/data_product_registry.py:92`（`intraday_board_flow_snapshots`，hot 120 天）、`runtime_task_registry.py:38-41`（`board_flow_curve`）；来源 `catalog.py:635-640`（`sector.flow_curve`：eastmoney_free 键下实为同花顺公开页，单位亿元；longhuvip 授权行业榜，单位元）；日特征 `app/database.py:929`（`sector_flow_daily_features`）、`app/ths_sector_flows.py:1-12`；实现 `app/board_flow_capture_actions.py:1-10`、`app/eastmoney_board_flow_curve.py:1-8`、`app/board_flow_units.py` | n8n 的板块键是同花顺板块名/Longhu 行业，不是东财 BK 代码；每分钟一份横截面快照（可拼出曲线），没找到按板块拉的"小/中/大/超大"五档分钟序列，只有净额；曲线起始日 FACTS 未记 | — |
| D16 | 交易日历 | 腾讯 sh000001 日K 日期序列（260 日），失败退工作日近似 | 已有 | 表 `quant.market_trade_calendar`（`app/database.py:468`，字段 exchange/calendar_date/is_open/pretrade_date）；使用 `app/market_history_backfill.py:138,302`；来源 `catalog.py:259,762-765`（`reference.trade_calendar`：tushare_super_get DORMANT、fuyao_ths `a_share_trading_days` DECLARED、tdx_public `tdx_holidays` DECLARED） | 现役来源均为 DORMANT/DECLARED；表的日期覆盖范围 FACTS 未记 | — |
| D17 | 龙虎榜（每日上榜明细：净买额、买卖额、原因、席位类型） | 东财数据中心 `RPT_DAILYBILLBOARD_DETAILSNEW`，收盘后按日取 | 已有 | `app/datasources/collectors/post_close.py:58`（`fuyao_dragon_tiger`，17:30–23:30 窗口）、实现 `:175-196`（`a_share_dragon_tiger_list`，`board_type=all`，按日期取；入 `market_events` 事件 + `lhb_hot_money` 席位观察）；`app/datasources/catalog.py:654-658`（`lhb.daily`：tushare 已 RETIRED、fuyao_ths DECLARED、akshare DORMANT；`lhb.seat_statistics` 注明"席位对应游资无官方来源，需自建映射"）；授权接口 `GetTopList` `app/longhu_capability_registry.py:53`（POST_CLOSE_ACTIONS）；落表契约 `data_product_registry.py:95`（`market_events`，warm 365 天）、`:73`（`longhu_supplemental_evidence`） | 历史深度 FACTS 未记，无法确认；n8n 无东财数据中心的 `RPT_DAILYBILLBOARD_DETAILSNEW` 报表（`rg "RPT_DAILYBILLBOARD"` 在 `quant-service/app` 无命中，东财数据中心只放行了解禁/股东/大宗/回购/业绩/IPO/两融等，`catalog.py:683-689`）；活跃来源 Fuyao 为 DECLARED 非 LIVE_VERIFIED | — |
| D18 | 集合竞价快照（全市场，五档，三轮采样，落盘） | 腾讯 qt 批量 + 东财 ulist.np 补，9:26/9:27/9:31 | 部分有 | 采集：`runtime_task_registry.py:97-101`（`auction_pool_capture`，09:15–09:26，"bounded"池）、`:50-56`（`auction_pulse`，2s，09:15–09:30，Longhu `MorningBiddingList` 等）、`:42-49`（`market_event_capture` 60s，全 A `a_share_auction_snapshot`）；`catalog.py:488-493`（`auction.open_snapshot` longhuvip LIVE_VERIFIED；`auction.close_snapshot` fuyao LIVE_VERIFIED）；FACTS F005（历史竞价榜 `morning_bidding_history` 2023-09-01 至 2026-09-18 共 739 天，只含竞价金额靠前约 200 只；逐只竞价快照/曲线/委托明细/处置表 2026-10-13 起才有）；FACTS F014（通达信 09:15–09:25 的 10 份文档 0 行）；定义 `app/auction_pool_runtime.py`、`app/auction_open_desk.py` | 全市场逐只竞价快照没有历史（2026-10-13 之前）；历史榜只有前约 200 只；全市场分钟快照 09:16 才开始、09:15–09:25 缺（B023）；app 的"三轮合并取额大者"口径 n8n 未采用（n8n 按时点存多份） | B007（历史榜未解出的列，等约 10 个交易日样本）、B023、B024（处置表缺 1 只）、B017 |
| D19 | 全市场代码表（主板非 ST，约 3052 只） | 新浪 hs_a 翻页，落 `auction/universe.json` | 已有 | 表 `quant.instruments`（`app/database.py:188-197`：symbol/exchange/name/industry/list_date/delist_date/is_st）；来源 `catalog.py:757-759`（`reference.instruments`，fuyao_ths `ticker_list`，DECLARED，"全 A 权威清单"，`app/market_universe_sync.py`）；`instrument_lifecycle_evidence`（`database.py:203`）保留上市/退市/ST 的时点证据 | — | — |
| D20 | 行业映射 + 上市日期 + 题材（行业）日聚合索引 | 东财 clist `f100,f26`；本地扫日K | 已有 | `instruments.industry`、`list_date`（`database.py:188-197`）；点时成分 `sector_membership_history`（`data_product_registry.py:93`，`database.py:1005`），来源 `catalog.py:588-611`（`sector.membership`）；回填任务 `runtime_task_registry.py:118-127`（`ths_member_backfill`、`all_board_member_backfill`）；题材日聚合：`N/docs/work/plans/tactic-trial-ledger.md` 的"ladder roles"段（`:379` 起）及 `app/concept_limit_strength.py`、`app/board_stock_mining.py` | n8n 的分类是同花顺/东财成分的时点历史，口径与 app 的"东财单一行业字段"不同；成分覆盖率 FACTS 未记 | — |
| D21 | 指数日线（上证等，320 日） | 腾讯 fqkline | 部分有 | `catalog.py:450-452`（`bars.index_daily`：longhuvip_index DORMANT → `canonical_bars_daily`；fuyao_ths `ths_index_prices_historical` DECLARED；tdx_public DECLARED）；`app/broad_etf_flow.py:50`、`app/market_timing.py:46` 自己也用腾讯 `fqkline` | 指数日线的覆盖区间 FACTS 未记；主绑定为 DORMANT/DECLARED，没有 LIVE_VERIFIED 的指数日线来源 | — |

统计：数据集 21 个（D01–D21）；已有 13（D01、D02、D03、D04、D06、D07、D08、D11、D12、D16、D17、D19、D20）；部分有 8（D05、D09、D10、D13、D14、D15、D18、D21）；没有 0。

没有判为"没有"的原因：每个数据集在 n8n 都找到了至少一个对应的表、任务契约或能力绑定。尤其要注意，"已有"只表示能力在，不表示质量或覆盖等同；有缺口的写在"缺什么"列。

## 不确定的地方

1. 涨停池历史窗口口径在 app 内部自相矛盾：E/services/zt-history.js:6-8 与 E/services/longtou.js:17 说只有约 15 天（后者自己的实测是 2026-09-14 以前返回 0 条），而 E/services/limitup-table.js:6 写"指定交易日，可查历史"。我没有联网验证，因此"约 15 天"只是代码注释的说法。
2. E/services/engine.js:428-464 的 `computeEmotion`/`computeDecision` 只读到了取哪些输入（pools/breadth/prevZTPerf），没有逐行核对情绪分公式；不影响数据清单。
3. `assets/board-codes.json`（E/services/board-codes.js:14）在 electron/ 目录外，未读，其板块数量和 BK 范围不明。
4. 本地文件"保留期"：在 electron/ 全树里我只 rg 了 `writeFile`/`unlink` 相关线索，没有看到清理逻辑，所以写"无自动清理"；没有逐文件确认 main.js 其余 2000 行里没有删除调用。
5. N 侧 D01–D03：FACTS F004 的"涨停榜（每天 5 张）"来自 Longhu 授权接口（表名 `longhuvip_history`、`limit_performance_history`，FACTS 未说明字段），我没有逐字段确认它含 app 用到的 `fbt/lbt/fund/zbc`；封板详情的字段能力来自 `catalog.py:570-576` 的 `limits.seal_detail` 说明，不是对表的实测。
6. N 侧 D17：FACTS 没有龙虎榜的覆盖事实，所以"已有"仅指日度明细的采集链路存在；历史深度、席位明细是否进库未验证。`post_close.py:175-196` 里 Fuyao 龙虎榜曾因取数不带日期而连续失败 274 次（该文件注释），之后已改为按日期取；当前是否稳定未验证。
7. N 侧 D14：试验台"成本读数"的实现代码我没有找到（`rg cost_share_below_close` 只命中 `tactic-trial-ledger.md`，在 `quant-service/app` 下无命中），所以只能引计划文档；它是否已跑出落库结果未知。
8. N 侧 D05、D15：n8n 板块资金流的 `eastmoney_free` 键下实际是同花顺公开页（`catalog.py:635-637`、`app/eastmoney_board_flow_curve.py:1-8`），与 app 的东财 BK 板块不是同一套分类，所以"部分有"是按用途近似判断，不是字段一一对应。
9. N 侧 D18：app 是"三轮合并取成交额大者"落成一份日文件；n8n 是按时点多份存。两者在"竞价最终值"的定义上是否等价，我没有核对（`app/auction_pool_runtime.py` 未读）。
10. 数据集的拆分粒度是我定的（例如把涨停异动转表并入 D01/D16，把回测缓存并入 D01/D11），不同拆法会改变 21 这个计数。
11. N 侧所有 FACTS 的新鲜期限是 14 天（`N/docs/work/FACTS.md` 开头），其中 F001–F008 的测量日是 2026-10-12/13，今天（2026-10-13）仍在期内；之后引用需重测。
