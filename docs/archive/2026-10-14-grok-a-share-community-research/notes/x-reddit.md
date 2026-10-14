# X 与 Reddit：A股量化、涨停短线与每日复盘（截至 2026-10-13）

范围只含 X 检索接口返回的帖子全文，以及检索工具抓到的 Reddit 页面正文。X 帖未再逐条打开 x.com 网页；日期用接口时间戳（GMT）。Reddit：`www.reddit.com` 在本环境解析到私网地址，`web_fetch` 被 SSRF 拦住；`old.reddit.com` 跳登录。下面 Reddit 条目标明「未能二次打开原页」，数字只来自检索正文，不另补作者用户名或互动数。检索正文没有用户名的，不编造。星标、基金净值、席位金额都是发帖人说法，没有打开 GitHub 或基金披露核对。没有任何一条是论文。公开帖一律不是业主或同行证据。

证据等级：(a) 帖内给出规则、样本或样本外数字的自述回测，未经第三方复核；(b) 经验、复盘叙事、产品介绍或未附来源的数字。谣言保持为谣言。

## 1. 覆盖情况

### X（主检索为 Latest，`since:2025-01-01`）

做了这些查询：

- `("A-share" OR "A shares" OR "A-shares") (quant OR quantitative OR backtest)`，Latest，8 条。命中英文产品帖和一条中英对照回测。
- `("涨停" OR "打板") (量化 OR 回测 OR 策略)`，Latest，8 条。多数是个股点评；真正写回测规则的只有少数账号。
- `(Tushare OR AKShare OR Qlib OR "量化私募") (A股 OR 量化 OR quant)`，Latest，8 条。AKShare / Qlib 多，Tushare 与私募几乎没出现（后另查）。
- `("龙虎榜" OR "集合竞价" OR "情绪周期") (量化 OR 复盘 OR 打板)`，Latest，8 条。复盘帖多，集合竞价的量化结果少。
- `("打板" OR "涨停") (回测 OR 复权 OR 停牌 OR "幸存者" OR "T+1")`，Latest，6 条。停牌多指当日个股，不是回测偏差论文。
- `("集合竞价" OR Qlib) (量化 OR 回测 OR A股)`，Latest，6 条。
- 语义检索：`A-share quantitative trading data pitfalls: adjusted prices, suspensions, limit-up cannot fill, survivorship bias, T+1 slippage versus live trading`，`from_date=2025-01-01`，5 条。返回的是美股 NQ / 加密的滑点讨论，没有 A 股复权或涨跌停无法成交的帖。记为未找到。
- `("幸存者偏差" OR "未来函数" OR 复权 OR "point-in-time" OR "停牌") (回测 OR 量化 OR A股)`，Latest，5 条。只有一条「错误复权」预告，其余是停牌消息或无关的模型压缩。
- `("量化私募" OR "私募量化") (A股 OR 回撤 OR 超额) since:2025-01-01 min_faves:5`，Latest，5 条。这是本轮唯一按互动过滤的查询。高赞内容是叙事和指控，不是回测。
- `Tushare (A股 OR 积分 OR 复权 OR 量化)`，Latest，4 条。有付费/积分和 Agent Skill 打包，没有数据质量实测。

未做：`mode=Top` 的「涨停/打板」专次。高互动样本只来自上面的 `min_faves:5`。

英文帖很少讨论打板、龙虎榜、情绪周期。英文里的 A 股量化，主要是个人开发者用英文写 CSI300/500/1000、baostock、AKShare、Qlib。中文帖分两层：低互动的每日涨停/龙虎榜复盘，以及较高互动的量化私募叙事（2026 年 3 月、8 月）。

### Reddit

检索式（站点限定经常被搜索扩到别的子版，下面按实际命中写）：

- `site:reddit.com/r/algotrading` China A-shares / limit up / T+1 / Tushare / AKShare / Shanghai / SSE。2024–2026 没有 A 股打板或 Tushare/AKShare 帖。返回的是美股回测、美国 LULD、yfinance。
- `site:reddit.com/r/quant` A-share / Tushare / Qlib。相关的只有 2025-03-15 一条上证涨跌停历史数据提问，版主删帖。
- `site:reddit.com/r/quantfinance` A-share / CSI1000 / Tushare / Qlib。相关的只有 2026-04-29 一条，把 CSI1000 写进 AI 编程代理对比。
- `site:reddit.com/r/ChineseStocks` 与 `site:reddit.com/r/China_stocks`：这两次查询没有返回这两个子版的帖。同名但无下划线的 r/ChinaStocks 有帖。
- `site:reddit.com` Tushare / AKShare / Qlib：没有带回这三个库的 Reddit 讨论。
- `"China-market data tooling" OR "point-in-time financials" A-share site:reddit.com`：没有定位到可引用的独立帖。

r/ChinaStocks 热帖列表里曾出现一段「China-market data tooling」：OHLCV、公司行为、point-in-time 财务、英文公告。没有独立 URL、作者和确切日期，不收录。

Reddit 上几乎没有中文短线术语（涨停板、打板、龙虎榜、集合竞价、情绪周期）。英文讨论集中在制度差异（涨跌停、T+1）和「用 AI 做研究流程」，不是打板策略失效。

## 2. 按主题

### 2.1 英文与中文如何谈 A 股量化

- 有人用英文把一套「因子挖掘代理」描述成只服务 A 股：CSI300/500/1000、baostock，可选提交 WorldQuant BRAIN；约 1.45 万行 Python 加 Rust；作者提交大约从 2026 年 4 月下旬到 5 月 20 日，之后停止。这是读者对仓库的转述，不是回测结果。证据等级(b)；https://x.com/MichaelBeeNFree/status/2110182687969620403 ；2026-10-14（GMT）；个人；摘录依据：帖文写 “built entirely for Chinese A-shares (CSI 300/500/1000, using baostock data)” 以及 “nothing since”。
- 同一天有英文帖对比同一「缩量回调」规则：标普 500 成分 3,042 个信号、持有 20 日平均 +1.88%（随机日 +0.97%）；A 股前 500 名 33,823 个信号、平均 −1.65%（随机 −0.11%）。自述、无代码。证据等级(a)；https://x.com/Sylar76529/status/2109936007802184021 ；2026-10-13；个人；摘录依据：帖内同时给出两边样本数和随机对照。
- 开源 ETF 动量轮动工具自述 2015–2026 年化 20.9%、最大回撤 −27.7%，并写「双成交规则、自测引擎」。未给出样本表。证据等级(a)；https://x.com/XER_JK/status/2108513806456365325 ；2026-10-09；个人；摘录依据：帖文原文即这两项绩效和 “dual fill rules”。
- 英文 Qlib 帖只讲通用流程（建数据集、训练、回测、评估、Online Serving）和 GitHub 免费，正文没有 A 股市场或 A 股绩效。证据等级(b)；https://x.com/quantscience_/status/2106775392451170324 ；2026-10-04；个人；摘录依据：线程第 2 条只描述 workflow，未出现 A-share / CSI。
- 中文介绍 Qlib：因子、LightGBM/Transformer、回测、组合、以及 RD-Agent 做自动因子；提醒回测不等于未来，要算成本、过拟合和实盘。没有 A 股数字。证据等级(b)；https://x.com/lumxss/status/2109343523375870071 ；2026-10-11；个人；摘录依据：帖文五点功能加一句风险提醒，无绩效表。
- Reddit 长文把 A 股制度写成：主板单日 ±10%，创业板和科创板 ±20%；触及涨停后买盘排队、没有愿意在限价及以下卖出的对手则不成交；T+1 意味着当天买入不能当天卖，没有传统意义上的日内止损。这是帖主的机制说明，不是交易所规则原文，也不是回测。证据等级(b)；https://www.reddit.com/r/economy/comments/1ro6sgp/how_to_actually_trade_the_chinese_stock_market_an/ ；检索标注 2026-03-08；社区帖；摘录依据：检索正文 “Part II” 写明 10%/20%、排队不成交和 T+1。未能二次打开原页。用户名不在摘录里。

### 2.2 涨停、打板、情绪周期、龙虎榜、集合竞价

- 涨停后缩量低吸的自述回测：5 年、74,201 个候选；规则是涨停后第 3–7 个交易日成交量缩到涨停次日 35% 以下则收盘买，放量、止损 −10% 或持有满 15 天卖，成本 0.3%。全样本胜率 45%，平均每笔约 +0.25%。缩量比 &lt;0.15 胜率 48.1%，0.30–0.35 为 43.5%。流通市值 &lt;30 亿平均 +0.56%，50–200 亿为负。上证在 20 日线上方平均 +0.69%，下方 −0.34%。最近一年同一规则平均 −0.57%。作者说因此不把它当战法。证据等级(a)；https://x.com/Sylar76529/status/2109993545297961435 ；2026-10-13；个人；摘录依据：帖内写了样本、规则、成本和分样本，并单列最近一年转负。账号简介写会记录未来函数、幸存者偏差、过拟合，但这条帖本身没有展示这三项的检验。
- 「打板/涨停 + 回测」的 Latest 结果里，多数帖是染料、连板个股的当日点评，不是策略回测。可核对到规则和样本的，仍是上一条缩量低吸，不是追板成交模型。查询见第 1 节。
- 有账号把「几十家涨停、时间都在午盘前后、小盘、瞬间拉板」说成量化批量拉板，并猜测随后集体砸盘。没有名单、没有成交数据。证据等级(b)；https://x.com/frankfenn1788/status/2109950321992868106 ；2026-10-13；个人；摘录依据：帖文是「我看了下」加「咋玩」，无表。
- 同账号次日又写「从龙虎榜看量化批量拉板，中间肯定有掉队的」，仍无席位表。证据等级(b)；https://x.com/frankfenn1788/status/2109617679598223686 ；2026-10-12；个人；摘录依据：正文把龙虎榜直接解释成量化拉板，无原始榜单链接。
- 盘后情绪模板会报连板数、一字数、最高板、涨停家数和题材。2026-10-13 这条写：连板 12、一字 2、最高 3 板、涨停 70，连板题材含大消费、AI 影视、大农业、化工、ST 板块。这是一天的计数，不是周期模型。证据等级(b)；https://x.com/afacome/status/2110021365868933487 ；2026-10-13；个人；摘录依据：帖文标题即「盘后复盘 10.13 情绪周期」并列出上述整数。ST 只作为题材名出现，没有 ST 规则讨论。
- 另一条 10 月 13 日复盘写连板龙头新华传媒「10 连板（停牌）」。停牌在这里是当日事件，不是「停牌如何污染回测」的讨论。证据等级(b)；https://x.com/zhaocaishijie/status/2110024105936241037 ；2026-10-13；个人；摘录依据：帖文第 1 条即该句。
- 龙虎榜长帖把 2026-10-12 描述成游资与量化对打，并给出席位净买入金额（例如莱美药业约 3.13 亿元等）。金额未对照交易所页面。证据等级(b)；https://x.com/mmmny5aaa/status/2109679117566452184 ；2026-10-12；个人；摘录依据：正文自称「龙虎榜整体呈现……」，数字来自该帖，不是公告原文。
- 集合竞价复盘会排 9:25 涨停委买前几名，并写封单从开盘到收盘的变化（例：某 9 连板开盘封单约 33.7 亿、收盘约 14.21 亿）。数字是帖主口径，帖内还注明其中一个收盘封单来自「证券时报口径」。证据等级(b)；https://x.com/COBRAYKT24/status/2109241901593637304 ；2026-10-11；个人；摘录依据：帖文分「截止 9 点 25 分」与日内时间戳叙述封单。
- 有人发「集合竞价后选出的股票在收盘的表现」，绩效在图片里，正文没有数字，故不引用收益。证据等级(b)；https://x.com/Touji0459/status/2109909542377284072 ；2026-10-13；个人；摘录依据：文字只有日期和「选出的股票 / 收盘的表现」。
- 短线账号用「昨天推荐、今天 T+1 兑现」做实盘对账，并给出自报涨幅（例如隔夜最高 +20.35%、+12.19%）。荐股口吻，无成交回报或第三方记录。只说明社区把 T+1 隔夜当作短线结算单位。证据等级(b)；https://x.com/dalongwaa/status/2109935183961235601 ；2026-10-13；个人；摘录依据：标题写「昨日标的正式结算（10-12 入选，今日 T+1）」。
- 有人把夜间手工统计涨停和连板梯队说成可被一句话生成的看板：涨停数、3 板/2 板/首板、板块热力、炸板率、封单排名。这是产品功能列表，没有样本外成绩。证据等级(b)；https://x.com/KmyWang/status/2109947347128229961 ；2026-10-13；个人；摘录依据：帖文列了这几项，bio 写 limit-up analytics 与 paper-trading，但该帖没有纸交易数字。
- r/quant 有人问能否找到上证触及涨跌停的历史日数据。评论只回复 Bloomberg、Refinitiv。版主以偏题删除，并指向 r/algotrading，称技术分析和个人交易不算该版的量化。证据等级(b)；https://www.reddit.com/r/quant/comments/1jbvlia/china_sse_data/ ；检索标注 2025-03-15；社区帖；摘录依据：正文是该提问，评论为 u/lampishthing 与 u/quant-modteam。未能二次打开。作者 u/worm1804。

### 2.3 Tushare、AKShare、Qlib、量化私募

- AKShare 被介绍成免费 Python 库，覆盖行情、财报、资金流、龙虎榜、北向、基金；帖主称 GitHub 2.2 万星、MIT。星标未核对。帖主同时说关键决策不要只信这一来源。证据等级(b)；https://x.com/Chenzeze777/status/2107118416113115419 ；2026-10-05；个人；摘录依据：帖文列举接口并写「2.2 万星」「别只信它一个」。
- 终端看盘工具 stock-tui 的介绍写数据走 AKShare、不要 API key。无质量测试。证据等级(b)；https://x.com/lksmlabc/status/2106807563312599049 ；2026-10-04；个人；摘录依据：帖文 “数据走 AKShare，免费、不要 API Key”。
- 多 Agent 练手项目被介绍为 AKShare + CrewAI，4 个角色，输出只是分析参考。证据等级(b)；https://x.com/Chenfacai0/status/2108582005659349227 ；2026-10-09；个人；摘录依据：帖文写 4 个 Agent 和「不构成实际投资建议」。
- 同线程回复：用 CrewAI 串过 A 股数据，最费神的是 AKShare 取数不稳。证据等级(b)；https://x.com/godsonde/status/2108583457282502909 ；2026-10-09；个人；摘录依据：一句经验，无失败率。
- 同线程另一回复把坑归到数据源：AKShare 限流，Agent 大量时间在重试；策略看起来好，实盘打脸；并点名 A 股 T+1、涨跌停、停牌容易被 Agent 忽略。这是提醒，不是测量。证据等级(b)；https://x.com/xbox_grok/status/2108583200780095980 ；2026-10-09；个人；摘录依据：回复原文列举这三项规则和限流。
- A 股数据 Skill 的介绍（被 2026-10-14 的帖引用，原帖 2026-07-03）：13 个数据源、28 个端点；README 称 V3.0 起移除 akshare；覆盖 K 线、五档、逐笔、龙虎榜、资金流等。星标与仓库未打开。证据等级(b)；https://x.com/Jolyne_AI/status/2072998290195701916 ；2026-07-03；个人；摘录依据：引用块写明「彻底移除了 akshare 依赖」和数据层清单。
- 另一帖称 TradingAgents-astock 星标到 3,668，功能是 A 股多 Agent，数据含龙虎榜/游资/解禁，7 个分析师按 A 股规则辩论。星标未核对。证据等级(b)；https://x.com/jack1191484/status/2109828226663186789 ；2026-10-13；个人；摘录依据：帖文把星标和功能写在同一句。
- finance-quant-skills 被说成 13 个 Agent Skill：AKShare、BaoStock、Tushare/聚宽、pywencai、MiniQMT/QMT、Backtrader/RQAlpha、AKQuant、通达信、投研报告。帖主写「不冒充交易系统，只是工具手册」。证据等级(b)；https://x.com/_zheergen/status/2099085178346127383 ；2026-09-13；个人；摘录依据：帖文编号列出这些库，并写 Tushare 在技能列表里。互动为接口所报：约 70 赞、38,833 次查看。
- 有人说完整数据若用 Tushare「可能需要对应积分」，项目本身 MIT、不收费。未说明哪项接口要多少积分。证据等级(b)；https://x.com/_zheergen/status/2099107322987700349 ；2026-09-13；个人；摘录依据：该句即全部依据。
- 讨论里有人说没做量化，Tushare「付费就能拿到」，并提到可在闲鱼租付费账号下载。这是获取渠道的说法，不是数据质量结论；租号涉及账号共用，此处只记录该说法，不提供做法。证据等级(b)；https://x.com/LongLongAlpha/status/2102566261498814962 ；2026-09-23；个人；摘录依据：回复原文同时出现「tushare 付费」和「闲鱼租个付费账号」。
- 短线交易者说数据用过 eltdx、Tushare，并把 Codex 接到本地同花顺只读行情；正在磨集合竞价、盘中扫描和盘后复盘，自认不成熟。证据等级(b)；https://x.com/chen7/status/2098800743914598557 ；2026-09-12；个人；摘录依据：帖文列举这三处数据和三阶段流程。
- 量化工程账号预告下一篇讲「错误复权」：纸面年化 300%，实盘归零。帖文写「第一篇明天发」，本次检索没有见到那篇正文，因此 300% 只是预告，不能当已发表结果。证据等级(b)；https://x.com/coinglebons/status/2110174183724568616 ；2026-10-14（GMT）；个人；摘录依据：全文只有这一句坑的预告。
- 自媒体称截至 2026-07-31，幻方量化 9 只展示产品里 8 只年内收益为负，余下一只（九章幻方中证 500 量化多策略 2 号）年内 +0.04%；并称 7 月单月净值跌超 20% 的情况并不罕见。同帖还写明汯精选 CTA 前 7 月亏损 10.78%，九坤元嘉中证 A500 指数增强 1 号年内收益近 8% 但 7 月跌超 11%。未打开基金披露。证据等级(b)；https://x.com/WorldCapitalAI/status/2084979091191804296 ；2026-08-05；个人；摘录依据：帖文写出截止日期和这些百分比。约 17 赞、10,583 次查看。
- 另一账号断言头部量化私募（点名幻方、九坤、明汯、灵均）超额主要来自价量技术因子、轻基本面，并说「量化就是赚散户的钱」。没有因子贡献表。证据等级(b)；https://x.com/cnfinancewatch/status/2037053336978153730 ；2026-03-26；个人；摘录依据：帖文是判断句，bio 虽写 PhD/Professor，本帖无论文。
- 同日前后有视频帖把「量化内幕」说成老鼠仓，并称散户的钱进了梁文锋口袋，同时复述上面的「重技术面」句子。这是指控，不是事实。证据等级(b)；https://x.com/GlobalMoney/status/2035525263715618993 ；2026-03-22；个人；摘录依据：开头即「暗访揭秘……其实就是老鼠仓」，无文书或成交证据。接口所报约 230 赞。不当作真事。
- 帖文以「据传」写某游资 10 亿亏剩 2 亿，并断言量化私募规模 1.8 万亿、美股量化 1 秒 15 笔而 A 股 1 秒 300 笔、手工打板已失效。速率和规模在帖内无来源。「据传」保持为传闻。证据等级(b)；https://x.com/Will_Yang_/status/2035205958251819458 ；2026-03-21；个人；摘录依据：原文有「据传」三字，其余数字并列在同一帖且无链接。约 188 赞。
- 2024 年英文 Reddit 转述：宁波灵均被沪深交易所限制交易；评论引用报道称约一分钟内卖出 25.7 亿元，账户冻结至 2024-02-22。未打开彭博原文。只作 2024 年社区转述，不是 2025–2026 的方法讨论。证据等级(b)；https://www.reddit.com/r/Superstonk/comments/1awgs05/china_bans_selling_of_stocks_at_market_open_and/ ；检索标注 2024-02-21；社区帖；摘录依据：评论写 Ningbo Lingjun、$357.4 million / 25.7 亿元与冻结至 2 月 22 日。未能二次打开。

### 2.4 回测和实盘、数据坑（帖里实际说到的）

- 上节 Sylar 两条是仅有的、把成本写进规则的 A 股形态回测：0.3% 成本后全样本接近零，最近一年为负；跨市场同一规则在 A 股前 500 为负、在标普为正。等级仍是自述 (a)，见 2.1 与 2.2 的 URL。
- ETF 帖把「双成交规则」写成防自我欺骗，但没有写出两套规则分别是什么，也没有实盘净值。见 2.1 的 XER_JK 链接。等级 (a) 仅限于帖内那两个绩效数字。
- AKShare 限流、T+1、涨跌停、停牌被点名为 Agent 实盘会踩的坑。见 xbox_grok，等级 (b)。
- 错误复权被说成能把回测做成年化 300%、实盘归零，但是预告。见 coinglebons，等级 (b)。
- 涨停价上「有买盘但没有卖方则不成交」、T+1 锁住隔夜，是英文长文的机制描述。见 r/economy 链接，等级 (b)。没有帖给出排队位置模型或冲击成本曲线。
- r/quantfinance 帖主称用 7 套 AI 编程代理做了一个截面因子平台，覆盖美股和 A 股 CSI1000；A 股侧 13 个因子、MA200 状态过滤、walk-forward；自述样本外夏普约 0.49（成本 20bps）、最大回撤约 −27%。美股侧称 Alpha158 + Alpha101 等在标普 500 和 ADV 前 1000/2000 上找不到 IC&gt;0.02 的单因子。无代码链接出现在摘录里。证据等级(a)；https://www.reddit.com/r/quantfinance/comments/1syvwmf/i_tested_7_ai_coding_agents_on_a_real_quant/ ；检索标注 2026-04-29；社区帖；摘录依据：检索正文写 “OOS Sharpe around 0.49 at 20bps cost, MaxDD around -27%” 和 IC 门槛。未能二次打开。用户名不在摘录里。
- r/QuantitativeFinance 有学生帖：用双均线回测比亚迪，征求意见。摘录里没有绩效数字，故不引用收益。证据等级(b)；https://www.reddit.com/r/QuantitativeFinance/comments/1nbxn2b/trading_strategy_backtest_on_chinese_byd/ ；检索标注 2025-09-08；社区帖；摘录依据：正文只说 “simple trading strategy based on SMAs” 和 README。未能二次打开。作者 u/AbleChocolate2320。

### 2.5 每日复盘与 LLM / Agent

复盘帖的共同骨架（各条仍是 (b)，URL 见 2.2）：涨停家数、连板高度、一字板、题材、封单或炸板、龙虎榜席位、集合竞价委买。没有人在这批帖里给出可复现的「情绪周期」状态机或历史胜率。

- 有人问谁真的把 AI 放进 A 股短线：看盘、复盘、检查自己的交易；数据是 Tushare、eltdx、同花顺只读。见 chen7，2026-09-12，等级 (b)。
- CrewAI+AKShare、a-stock-data Skill、TradingAgents-astock、finance-quant-skills、Qlib+RD-Agent 的提及，都是工具介绍。见 2.3。等级 (b)。
- r/ChinaStocks：用 n8n、GPT-4o、钉钉做 A 股分析机器人。帖主列出的困难是数据拆在多个 App、东方财富等 API 会拦海外 IP（403）、F10 和新闻太长。流程是钉钉发代码，再拉上证指数、PE/PB/市值、主力净流入、MA5/10/20 和新闻；用 Cloudflare Worker 做代理。这是搭建说明，不是策略绩效。证据等级(b)；https://www.reddit.com/r/ChinaStocks/comments/1pn0yby/i_built_an_ai_stock_analyst_for_the_chinese/ ；检索标注 2025-12-15；社区帖；摘录依据：检索正文的三条痛点和 n8n 步骤。未能二次打开。用户名不在摘录里。
- r/mcp 有一帖标题宣布 query_table：MCP 抓取同花顺、通达信、东方财富表格。正文在检索结果里几乎只有 “Link”，仓库未打开，不引用其数据覆盖范围。证据等级(b)；https://www.reddit.com/r/mcp/comments/1jc2puu ；检索标注 2025-03-15；社区帖；摘录依据：标题即全部可用正文。作者 u/Weary-Database-8713，分数 1。未能二次打开。

## 3. 和五个研究主题的对应

### 数据坑

帖里明确点到的只有：AKShare 不稳和限流（godsonde、xbox_grok，2026-10-09）；不要单信免费爬虫源（Chenzeze777，2026-10-05）；Tushare 积分/付费门槛（_zheergen、LongLongAlpha，2026-09）；接口参数常变、有项目因此去掉 akshare（Jolyne_AI，2026-07-03）；错误复权被预告为致命但未给出那次回测（coinglebons，2026-10-14）；涨停价没有对手方则买不到、T+1 不能当天砍仓（r/economy，2026-03-08）；海外 IP 访问东方财富被 403（r/ChinaStocks，2025-12-15）；上证涨跌停历史数据在 r/quant 被当成非专业问题删掉，回复是买彭博或 Refinitiv（2025-03-15）。

未找到可引用帖：复权公式对比、停牌填充规则、ST 过滤、北交所、point-in-time 财务、幸存者偏差的量化结果、分钟/tick 质量。Sylar 的简介声明会记未来函数和幸存者偏差，但 2026-10-13 那两条回测没有展示这些检验。语义检索「A-share data pitfalls」返回的是美股和加密滑点，不是 A 股。

### 打板 / 情绪策略及失效

唯一带样本的「涨停之后」结果是缩量低吸，不是打板：全样本扣成本后约 +0.25%/笔，最近一年 −0.57%（Sylar，2026-10-13，等级 a，自述）。跨市场同一规则在 A 股前 500 为负（同作者另一帖）。社区口头上的「失效」是：午盘小盘集体涨停被说成量化拉板、散户做不过（frankfenn1788，等级 b）；「据传」加无来源的每秒笔数，用来论证手工打板失效（Will_Yang_，等级 b，不当事实）。情绪周期帖是每日计数（连板、一字、最高板、涨停家数），没有周期状态的历史收益。没有找到打板队列、封板失败率的样本外回测。KmyWang 只是把炸板率放进看板功能列表。

### 回测与实盘

自述回测里写了成本或成交约束的：Sylar 的 0.3%；XER_JK 的「双成交规则」但未定义；r/quantfinance 的 CSI1000、20bps、样本外夏普约 0.49、回撤约 −27%（2026-04-29，未能二次打开）。实盘落差都是叙述：AKShare 策略实盘打脸、Agent 忽略 T+1/涨跌停/停牌；复权错误导致纸面高收益实盘归零（预告）；短线号用次日 T+1 涨幅当对账。没有 A 股排队位置、容量或冲击成本的测量帖。Qlib 介绍只提醒「回测要考虑交易成本、过拟合和实盘」，无数字（lumxss，2026-10-11）。

### 每日复盘

2026-10-11 至 10-13 的中文帖把复盘做成固定字段：情绪（连板/一字/最高板/涨停数/题材）、集合竞价封单排名、龙虎榜席位净额、连板龙头是否停牌。见 afacome、COBRAYKT24、mmmny5aaa、zhaocaishijie。KmyWang 用英文说要把这套夜间 30 分钟的手工统计自动化。chen7 想把同一流程交给 Grok/Codex，但写明还没成熟。这些都是 (b)。没有帖比较「复盘字段」对次日收益的预测力。

### LLM / agent

2026 年讨论从「能不能用模型选股」转到「把数据接口封装成 Agent 技能」：CrewAI+AKShare 被作者自己的回复圈否定为数据源不稳；a-stock-data、finance-quant-skills、TradingAgents-astock 是技能或投研框架介绍；英文 Quant GPT 帖描述 baostock 上的因子挖掘代理，并称 2026 年 5 月 20 日后无提交；Qlib 被和 RD-Agent 一起提到，无 A 股成绩；Reddit 一侧是 n8n+GPT-4o 钉钉机器人（2025-12-15）、7 个编程代理做 CSI1000 因子平台（2026-04-29，唯一带样本外夏普的）、以及一个几乎只有标题的同花顺/通达信/东财 MCP（2025-03-15）。没有帖证明某个 Agent 在 A 股实盘扣费后仍有超额。

## 4. 查不到的来源清单

- r/algotrading，2024–2026 的 A 股量化、涨停、T+1、Tushare、AKShare：未找到。查询：`site:reddit.com/r/algotrading` 配 China A-shares / limit up / T+1，以及 `("A-share" OR Tushare OR AKShare OR Shanghai OR SSE) (backtest OR quant OR data)`。返回的是美股回测和美国 LULD。
- r/ChineseStocks：未找到。查询：`site:reddit.com/r/ChineseStocks` 配 quant / limit up / A-share / Tushare。
- r/China_stocks：未找到。查询：`site:reddit.com/r/China_stocks` 配同样关键词。存在帖的是 r/ChinaStocks（无下划线）。
- Reddit 上的龙虎榜、集合竞价、情绪周期、打板回测、Qlib、Tushare、AKShare：上述站点检索未返回这些主题的帖。
- r/quant 除 2025-03-15 涨跌停数据提问（被删）外，没有 Tushare/Qlib/打板帖。r/quantfinance 除 2026-04-29 CSI1000 那条外，命中的是 Alpha Vantage、Alpaca，与 A 股无关。
- X 语义检索「adjusted prices, suspensions, limit-up cannot fill, survivorship, T+1 slippage」：5 条都不是 A 股。未找到。
- 专门讨论北交所、ST 样本、分钟或 tick 质量、排队位置、point-in-time、幸存者偏差测量的 2025–2026 帖：关键词「复权 / 未来函数 / 幸存者偏差 / point-in-time / 停牌」的 Latest 没有给出这类结果。r/ChinaStocks 列表上有过 point-in-time 片段，但没有独立 URL，不收录。
- Tushare 的复权、停牌、积分档位实测：未找到。只有「要积分/要付费」和技能清单。
- 打板策略本身的样本外回测（相对涨停后低吸）：`打板 + 回测` 的 Latest 没有这类帖。
- 论文：本次 X 与 Reddit 检索没有可打开的论文页。cnfinancewatch 的头衔不能把该帖升级成论文。
- 量化私募的基金披露原文、GitHub 星标、交易所龙虎榜原文：帖里有数字，原件未打开，故不把这些数字写成已核实事实。
- `mode=Top` 的涨停/打板检索：未做。高互动只来自 `量化私募 min_faves:5` 的 Latest。
- Reddit 原页：全部未能二次打开，原因见第 1 节。若后续能打开，应复核夏普 0.49、±10%/±20%、灵均冻结金额这三处数字是否与页面一致。
