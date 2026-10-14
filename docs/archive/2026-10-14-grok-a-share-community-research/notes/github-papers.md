# A股量化：开源项目与 2024–2026 论文

检索日：2026-10-13。只记录本次打开页面或检索工具返回的原文摘录。不引用未打开的绩效数字，不把论文结论改写成策略。

证据等级：(a) 仓库/论文/issue 自己给出数据、代码或测得结果；(b) README 或叙述，没有可复核的结果表。

## 这些项目写明了哪些 A 股数据假设（复权、停牌、股票池、PIT）

### Takeaway

官方 Qlib 的中国日频包被 README 标成暂时停用，并改指向社区数据包；文档里的中国模式只有单一涨跌停阈值 0.099 和 100 股整手，停牌被写成 NaN，现成下载不是完整的 PIT 财务库。AKShare、BaoStock、旧 TuShare 是行情接口而不是回测引擎，复权口径和停牌填法彼此不同。TradingAgents 上游与 FinGPT-Forecaster 的已发布实验不是 A 股。

### Cited Findings

| 名称 | URL | 日期 | 作者类型 | A股能做什么 | 文档里写明的限制 | 证据 |
| --- | --- | --- | --- | --- | --- | --- |
| Microsoft Qlib | https://github.com/microsoft/qlib/blob/main/README.md ；数据层 https://qlib.readthedocs.io/en/latest/component/data.html | README 列出的最近正式版 v0.9.0 为 2022-12-09；主分支页面在本次打开时仍写“official dataset is disabled temporarily”。文档站标题为 QLib 0.9.8.dev11，检索工具给 data.html 的时间是 2025-11-10，页面本身无日历日期。PIT 数据库在 README 功能表中标为 2022-03-10 发布 | 机构 | 中国股票模式、Alpha158/Alpha360、csi300 等股票池、日频与 1 分钟示例、表达式因子和 ML 工作流。停牌约定：OHLC、volume、money、factor 置 NaN。复权价在每只股票第一个交易日归一成 1，原始价用 `$close/$factor`。股票池是代码加日期区间；API 文档举例的市场名包括 all/sse/szse/sse50/csi300/csi500 | 同一 README 先说官方数据因数据安全政策暂时停用，并给出社区包 `chenditc/investment_data` 的 `qlib_bin.tar.gz`，下文仍保留 `python -m qlib.cli.data ... --region cn`，并写明数据来自 Yahoo Finance，“might not be perfect”；离线包不能在其上去做增量更新，要由 Yahoo collector 从头下载。中国模式交易单位 100、limit threshold 0.099，文档没有按主板/创业板/科创板/北交所/ST 分档。用户要自己把 PE、EPS 等并进 CSV 再 dump。这不是现成的 PIT 财务库 | (a) 文档与代码约定；(b) “resume in short future” 没有日期 |
| Qlib 数据 issue：成交量单位 | https://github.com/microsoft/qlib/issues/2362 | 检索结果标注 2026-10-04 | 个人（issue） | 报告官方 CLI 仍能下到 `SunsetWolf/qlib_dataset` 的 `qlib_data_cn_1d_latest.zip`，该包结束于 2020-09-25 且没有 `$amount`；在该旧包上，用 3 只股票、2020-09-14 至 2020-09-18、n=15，参与率比值为 1.0000。另一条 CN 管道把 volume 乘了首日收盘价后，参与率中位数约 100.10，冲击成本比约 10,020 | 这是 issue 作者的测量，不是 Qlib 发布的基准表。说明“同一 `$volume` 字段在不同 CN 包里单位不一致”，会把成交占比和冲击成本放大约两个数量级 | (a) issue 内的测量；(b) 未由项目方在 README 复述 |
| Qlib 成分股截止日期 | https://github.com/microsoft/qlib/issues/1700 | 2023-11-26 | 个人（issue） | 询问如何更新 CSI300 成分 | 提问者称随包的 `csi300.txt` 只到 2020-09-25；维护者指向 `scripts/data_collector/cn_index`，issue 随后关闭。未在该页给出更新后的截止日期 | (b) 用户观察加维护者指路，没有新的测量表 |
| Qlib 停牌与过滤器 | https://github.com/microsoft/qlib/issues/2084 | 检索结果标注 2026-01-13 | 个人 / 维护者回复 | 说明 `list_instruments` 只看 `instruments/csi300.txt` 的日期区间 | 回复称停牌日可以没有 feature，但成分文件仍把股票标成 active，`ExpressionDFilter` 会在缺失的 `$close` 上过滤。页面举例 `SZ002049` 的成分区间写到 2026-01-13，而 close bin 只到 2025-12-29。这是元数据与行情不对齐，不是复权问题 | (a) 对该行为的代码路径说明；样本是 issue 中的个例 |
| chenditc/investment_data（Qlib README 指向的社区包） | https://github.com/chenditc/investment_data ；涨跌停表 https://github.com/chenditc/investment_data/blob/main/docs/final_a_stock_limit.md | 打开的 README 写到 `repair-2026-07-20` 这一运维操作，页面未显示 commit 日期 | 个人 | 把 Dolt 中的行情导出成 Qlib `qlib_bin.tar.gz` 与 manifest。日更需要 Tushare token。源前缀：w/c 为 Wind、财汇，只到 2019；另有 ts、ak、yahoo、baostock 和合并后的 final。目标包括补退市公司。复权：各源用自己的 adjust factor，再用 link table 把调整价对齐到 final | 涨跌停表文档原文写删除早于 `"1996-12-196"` 的数据（字符串如此，未在页面改成 1996-12-16）；用 Tushare 涨跌停覆盖，再用 BaoStock 的 ST 信息填上下限；高低价与涨停价差小于 1% 时把涨停价改成最高价，差大于 1% 则删行并当成当天无涨跌停。校验两条并列写成 high ≤ up_limit 且 high ≥ up_limit，页面未解释这个矛盾 | (a) 构建与校验规则写在仓库文档；(b) 没有在本次打开的 README 给出与交易所逐日对账的误差表 |
| microsoft/RD-Agent | https://github.com/microsoft/RD-Agent ；论文 https://arxiv.org/html/2505.15155v2 | 论文 v2：arXiv:2505.15155v2，2025-09-25；README 写 NeurIPS 2025 接收。仓库页未显示单独的 commit 日历，新闻表含更晚的非量化论文条目 | 机构 / 论文 | 量化场景调用 Qlib 做因子–模型联合搜索。主实验是 CSI300：训练 2008-01-01–2014-12-31，验证 2015-01-01–2016-12-31，测试 2017-01-01–2020-08-01。扩展实验为 CSI500 与 NASDAQ100，训练 2008–2021、验证 2022–2023、测试 2024 至 2025 年 6 月。日频多空，按预测收益排序，论文写有换仓、持有和 “realistic transaction costs” | README 写当前只支持 Linux，且多数场景要 Docker。论文说 LLM 不看原始行情、只看 schema。打开的正文没有印出佣金、印花税或滑点的具体数值；“under $10” 是论文设定下的计算/API 成本，不是交易成本。不包含涨停、情绪或集合竞价实验 | (a) 论文报告了 IC 与策略指标 |
| AKShare | https://github.com/akfamily/akshare ；数据说明 https://akshare.akfamily.xyz/data_tips.html ；股票文档 https://akshare.akfamily.xyz/data/stock/stock.html | README 的 bibtex 年份是 2022。数据说明页标题为 AKShare 1.19.1，检索工具标注 2026-09-30。仓库页未显示最新 commit 日期 | 个人（Albert King、Yaojie Zhang） | 沪深京 A 股日线/分钟线接口，`adjust` 可为空、`qfq`、`hfq`，部分接口还可返回复权因子。另有停复牌、新闻等接口。README 示例为 `stock_zh_a_hist` 不复权 | 数据说明用可复现片段写出 `600734` 在 2005-05-09 至 2005-05-20 的后复权开高低收出现负数。股票文档写多次拉取容易封 IP；1 分钟数据只返回近 5 个交易日且不复权。停复牌示例是百度日历的单日查询，不是把停牌日写进 K 线的统一约定。README 声明只供学术研究，接口可能被删。库本身不是回测器，也没有 PIT 股票池 | (a) 文档给出了负价格的输入与输出表 |
| VeighNa / vnpy | https://github.com/vnpy/vnpy | README 与版本号 4.5.0 的提交显示为 2026-10-06 | 开源项目（社区） | A 股交易接口包括中泰 XTP、华鑫奇点 TORA；行情可接 RQData、迅投研。`vnpy.alpha` 用 Alpha158（注明来自 Qlib），notebook 用 RQData 或迅投研下载指数成分变化和 K 线，再做 Lasso/LightGBM/MLP 研究流。CTA、组合策略有回测应用 | 打开的 README 没有写 T+1、分板块涨跌停、停牌填价或 PIT 财务。A 股研究示例依赖商业/外部数据服务，不自带可复现的免费全市场包。不是涨停或集合竞价研究框架 | (b) 能力清单；(a) 仅限 README 点名的模块路径 |
| TauricResearch/TradingAgents | https://github.com/TauricResearch/TradingAgents ；论文 https://arxiv.org/abs/2412.20138 | 仓库新闻：v0.6.0 写于 2026-10，最新展示提交 2026-10-03。论文 2024-12-28 投稿，v7 修订 2025-06-03 | 机构 / 论文（Tauric Research） | 多代理基本面、情绪、新闻、技术分析，并有回测日期网格。v0.5.0 新闻写全路径 point-in-time，SEC 财报按申报日提供；v0.5.2 写回测只能看到每个分析日已发布的数据 | 打开的 README 数据源是 Yahoo、SEC EDGAR、FRED、StockTwits、Reddit、Alpha Vantage 等。情绪来自新闻标题、StockTwits 和 Reddit。没有 A 股复权、停牌、涨跌停或 T+1。v0.2.5 只写 “non-US alpha benchmarks”，没有点名 A 股。论文摘要也没有 A 股样本 | (a) 仓库声明了 PIT 规则，但是美股/通用数据路径；(b) 摘要声称收益、夏普、回撤改善，打开的摘要没有样本窗和成本 |
| hsliuping/TradingAgents-CN | https://github.com/hsliuping/TradingAgents-CN | 打开的是社区版 v3.0 README，页面未显示 commit 日期 | 个人 | 面向 A 股的研究助手：单股辩论、自然语言筛选（写“6000+ 只”）、纸面交易。登录后要自行配置行情 Token | 混合许可：核心引擎开源，应用层专有；Pro 才有策略创建/优化。打开的 README 没有复权、停牌、涨跌停、PIT 或样本外回测表。纸面交易不等于带成本的策略回测 | (b) |
| AI4Finance-Foundation/FinGPT | https://github.com/AI4Finance-Foundation/FinGPT | README 的 “What's New” 停在 2023-11 的 Forecaster 发布；更早条目为 2023-06 至 2023-10 | 机构（AI4Finance Foundation） | README 写 FinGPT v1.x “designed for Chinese markets”。Forecaster 数据集 URL 指向 DOW30、2023-05 至 2024-05。情绪模型评测表是 FPB、FiQA-SA、TFNS、NWGI | 同一 README 写 Forecaster “currently trained on US market data (DOW 30)”；中国市场需要另采数据并微调。打开的 README 没有 A 股复权、停牌、股票池或涨停回测 | (a) 市场范围和美股数据集链接；(b) v1 适用于中国市场这一句没有伴随 A 股结果表 |
| FinNLP 的中国新闻源（FinGPT 文档站） | https://ai4finance-foundation.github.io/FinNLP/zh/ | 检索工具标注 2023-12-28 | 机构 | 表中列出新浪、东方财富新闻对应中国股票；数据集行 “AShare / 新闻 / 3680 / 2018-07-01 到 2021-11-30” | 这是数据源清单，不是行情复权或交易回测。本次只使用检索摘录，没有再打开全文核对字段定义 | (b) 表内计数没有在本次打开的方法节里复核 |
| BaoStock K 线说明 | https://baostock.com/mainContent?file=stockKData.md | 检索工具标注 2026-09-16。2026-10-13 再次打开该 URL 只返回 “×”，下面内容来自第一次检索摘录 | 机构（BaoStock 站点） | 日线可取 1990-12-19 至当前；不复权、前复权、后复权。`adjustflag` 默认 3 不复权，1 后复权，2 前复权。自称使用涨跌幅复权法 | 停牌日：开高低收都等于前一交易日收盘价，成交量与成交额为 0，换手率为空。不同系统复权法不一致。页面没有 PIT，也没有策略回测 | (a) 若以第一次摘录为准；第二次打不开，见文末 |
| waditu/tushare（旧仓库） | https://github.com/waditu/tushare | 页面显示最新 commit 2020-03-04；README 变更日志写 Pro 第一稿 2018-08-10，1.2.17 为 2018-11-24 | 个人 / 早期开源项目 | `get_h_data` 分前复权、后复权、不复权；默认前复权，不设日期时返回近一年。README 指向后续官网 https://tushare.pro | 仓库停在 2020。复权接口建议区间不要超过一年并本地存储。打开的 README 没有停牌填法、PIT 或涨跌停表。tushare.pro 本次未登录、未打开接口文档 | (a) 旧接口约定；(b) Pro 的现行字段以未打开的官网为准 |
| guoyaohua/limit-up-sniper | https://github.com/guoyaohua/limit-up-sniper | 打开的 README 未显示最新 commit 日期（页面显示 47 commits） | 个人 | A 股首板研究：排除科创板、北交所、ST、停牌、上市不足 100 天；250 日涨停基因；14:50 后不新买。模拟撮合写明 T+1、100 股、下一 Tick、五档 VWAP、滑点、佣金、最低佣金、印花税、过户费。排板成交要求下单后新增成交量穿过当时队列，撤单不算成交 | 依赖 Windows 上的 QMT/XTQuant，不在 PyPI。默认 simulation，实盘需另入口并输入 yes。板块映射首次要登录问财。打开到运行说明为止，没有看到一份带起止日期、样本外窗口和绩效表的已发表回测 | (a) 规则与撮合口径写在 README；(b) 没有已公布的绩效结果 |

### Inferences

- 把 Qlib 中国示例直接当成“可交易的 A 股回测”会同时踩到三件文档已写明的事：官方包停更或只到 2020-09-25、复权价被归一、涨跌停只有 9.9% 一档。创业板、科创板、北交所和 ST 不能从这一个 `limit_threshold` 推出来。
- 社区包 chenditc 是 Qlib README 当前点名的替代品，但它的涨跌停表会改价、删行，并把差超过 1% 的日子当成“无涨跌停”。这和交易所涨跌停价不是同一概念。
- AKShare 的后复权负价格是文档自己复现的，不能把 `hfq` 当成全历史都可用的价格。BaoStock 摘录则把停牌日填成“价平、量为 0”，和 Qlib 的 NaN 约定相反；混用会把停牌看成可交易或零收益。
- PIT 在这些项目里不是同一件事。Qlib 有 PIT 接口和 2022 年的功能发布，但现成中国包没有被写成财务 PIT。TradingAgents 上游的 PIT 是美股申报日与新闻日期，不能挪到 A 股。

### Gaps

- Qlib LightGBM 的 `workflow_config_lightgbm_Alpha158.yaml` 页面只返回了“71 行”的外壳，正文未渲染，因此没有引用其中的 `open_cost` / `close_cost`。不能把记忆中的费率写成文档事实。
- RD-Agent 论文附录 C.4 的具体费率没有出现在已打开的匹配段。
- AKShare、BaoStock、TuShare 都没有在已打开页面写明“财务数据按公告日而不是报告期可用”。
- `xu-duqing/investment_data` 与 `xu-duqing/deep-alpha` 只在检索摘录中出现，没有单独打开仓库页，不记入上表。

## 有没有带成本、样本外窗口、可复现的涨停 / 情绪 / 集合竞价回测

### Takeaway

指定项目里，没有一个在已打开的 README 或论文正文中给出“涨停或集合竞价策略 + 显式交易成本 + 样本外窗口”的可复核结果。RD-Agent(Q) 有可复现入口和样本外日频多空，但是因子/模型实验，不是涨停、散户情绪或集合竞价。limit-up-sniper 写了成本和 T+1 撮合，但没有公布那次回测的绩效表。

### Cited Findings

- Qlib 的中国基准是 Alpha158 一类日频预测。数据层示例把 Alpha158 配成 `csi300`、2008-01-01 至 2020-08-01，这与 RD-Agent 采用的切分一致；它不是涨停或集合竞价实验 — [Qlib data.html](https://qlib.readthedocs.io/en/latest/component/data.html)，[RD-Agent 论文](https://arxiv.org/html/2505.15155v2)。
- RD-Agent(Q) 在 CSI300 测试段（2017-01-01 至 2020-08-01）报告：只优化因子时，IC 最高到 0.0497，ARR 最高到 14.61%，因子数量少于 Alpha158/360。正文还写相对经典因子库 ARR 约 2 倍、因子少 70% 以上。CSI500 与 NASDAQ100 的测试是 2024 年至 2025 年 6 月，Table 2 给出 IC、ICIR、IR、MDD，不给涨停策略。论文写了 realistic transaction costs，但已打开段落没有费率数字。代码入口是仓库 README 指向的 quant 文档 — [论文](https://arxiv.org/html/2505.15155v2)，[仓库](https://github.com/microsoft/RD-Agent)。这是策略回测，但对象是日频多空因子组合。
- TradingAgents 上游 v0.5.x 声称回测不看未来数据，情绪源是 StockTwits/Reddit，不是东方财富股吧，也没有 A 股集合竞价 — [README](https://github.com/TauricResearch/TradingAgents)。
- TradingAgents-CN 社区版有“模拟交易与复盘”，没有样本区间、费用或样本外表 — [README](https://github.com/hsliuping/TradingAgents-CN)。
- FinGPT-Forecaster 的已发布数据集是道琼斯 30、2023-05 至 2024-05，README 明确中国市场不在该模型训练范围内 — [README](https://github.com/AI4Finance-Foundation/FinGPT)。
- limit-up-sniper 把佣金、最低佣金、印花税、过户费、滑点、整手和 T+1 写进模拟成交，并建议先跑至少 20 个交易日的实时模拟。这不是一份已经跑完、可按窗口复核的样本外报告 — [README](https://github.com/guoyaohua/limit-up-sniper)。
- chenditc 有 `final_a_stock_limit` 表和“触板后回测不能再成交”的动机说明，仓库本身不是策略回测 — [limit 文档](https://github.com/chenditc/investment_data/blob/main/docs/final_a_stock_limit.md)。

### Inferences

- “可复现的 A 股回测”和“可复现的涨停/情绪/竞价回测”不是同一件事。前者 RD-Agent 与 Qlib 工作流有；后者在本次打开的指定仓库里没有结果表。
- 社区打板仓库即使写了费用，只要绩效只存在于用户本机的 `output/` 或未公开的运行，就不能当成已经发表的样本外证据。

### Gaps

- 没有打开到 limit-up-sniper README 的后半。若后半有绩效表，本次笔记没有覆盖。
- 下列仓库只在检索摘要里出现，未全文打开，因此不采用它们的胜率或收益：`Fidingks/Mao-Quant`（摘要写内置 T+1 与分板块涨跌停）、`sqyangx/quantlab`、`68110923/xunlongjue_doc`、`cy-Yin/TradingAgents-CN-lite`、`simonlin1212/TradingAgents-astock`、`yang1729346/TradingAgents-A-Share`、`touhoufan2024/qlibAssistant`、`vnpy/vnpy_tushare`。
- `vnpy_tushare` 的检索摘录写：股票覆盖 SSE、SZSE、BSE，且“期货第一条夜盘 K 线是集合竞价，可自行过滤”。这是期货夜盘，不是沪深股票开盘集合竞价。未单独打开该 README，不作为已核实项目。

## 近期论文实际报告了什么（涨跌停、散户情绪、集合竞价、T+1）

### Takeaway

已打开的 2024–2026 论文没有把涨停打板、股吧情绪或开盘集合竞价做成一套带成本的交易策略。它们分别报告：涨停命中可以被分类器轻度预测；股吧隔夜情绪与上证 180 开盘集合竞价的定价效率正相关；CSI500 的 5 分钟羊群与融资、融券方向相反；多主题帖子情绪比单一情绪更能解释流动性；T+1 改变日内/隔夜收益或期权平价缺口。样本窗口和“是不是策略回测”以各页原文为准。

### Cited Findings

| 题目 | URL | 日期 | 作者类型 | 样本 | 作者实际报告的结论 | 是否策略回测 |
| --- | --- | --- | --- | --- | --- | --- |
| Stock Price Limit and Its Predictability in the Chinese Stock Market | https://doi.org/10.1002/for.3197 | 首次发表 2024-09-12（Journal of Forecasting, 2025） | 论文 | 摘要未写起止年份。对象是中国股市涨跌停命中，按个股训练，并比较老股票与第三产业股票 | 用过去收益做特征、对多数类下采样后的轻度集成模型，把涨跌停命中的平衡分类准确率从朴素猜测的 50% 提高到约 66%。老股票和第三产业股票更可预测。摘要把这解释为更长历史的股票更易受投机影响 | 否。是分类预测，不是交易回测 |
| Overnight investor sentiment, trading behaviors, and pre-opening price efficiency | https://doi.org/10.1016/j.jbef.2026.101267 ；https://www.sciencedirect.com/science/article/abs/pii/S2214635026001292 | 期刊日期 2026-12（Volume 52, article 101267）。作者 Zhaoyuan Gu、Xijuan Xiao、Ryuichi Yamamoto | 论文 | 方法节可见文字：2022-10 至 2024-09；上证 180 的 180 只成分股；股吧个股隔夜情绪；集合竞价逐笔 | 隔夜情绪对开盘前集合竞价的定价效率有显著正影响；渠道是更激进的买单和更少的买方有意羊群，买卖两侧不对称，作者归因于卖空约束。信息更透明的股票上更强。全文表格在付费墙后，未打开 | 否。是定价效率回归，不是下单策略 |
| Intraday herding drivers in China’s A-share market: evidence from the China Securities Smallcap 500 Index | https://doi.org/10.1016/j.frl.2025.108292 | Finance Research Letters，2025-11，Volume 85 Part E，article 108292。作者 Jingxing Guo、Xingguo Luo、Liu Yang | 论文 | CSI500 成分的逐笔数据，5 分钟，分买方发起、卖方发起和中性。摘要未写日历样本期 | 情绪和注意力对个股羊群有解释力。作者定义的相对融资兴趣（偏散户）放大羊群，相对融券兴趣（偏机构）减弱羊群。前 5 分钟涨得多会鼓励买侧羊群、抑制卖侧羊群 | 否 |
| Deep learning-based multi-dimensional investor sentiment and stock liquidity: Evidence from China | https://doi.org/10.3934/QFE.2025026 | 收稿日期 2025-04-21，修订 2025-08-04，接收 2025-09-03，发表 2025-10-11。作者 Zhiyi Wang、Jingru Guo、Gaoshan Wang、Xiaohong Shen | 论文 | 摘要写 1,100 万条 A 股投资者帖子，拆成政策、心理、社会、气候、技术和公司六类。未写帖子的起止日期，也未写蓝筹与科技样本的股票名单 | 六维情绪比单一总指数更能解释流动性波动。蓝筹上社会、心理、公司情绪对流动性有持续正影响；科技股对心理、技术和公司情绪更敏感，且更非线性 | 否。因变量是流动性，不是策略收益 |
| Unlocking the true price impact: Intraday liquidity and expected return in China’s stock market | https://doi.org/10.1016/j.pacfin.2025.102939 ；https://ideas.repec.org/a/eee/pacfin/v94y2025ics0927538x25002768.html | Pacific-Basin Finance Journal，2025-12，Volume 94。作者 Hang Cheng、Yongdong Shi、Zhang Tong | 论文 | 摘要未写股票范围和年份。制度背景写到 T+1 禁止日内回转，从而改变交易者行为 | 时间加权的日内 Amihud（TIAM）比日频流动性代理更能隔离交易造成的价格冲击。由此做成的规模中性日内非流动性因子 IML “earns a significant alpha”，且能解释一部分规模与情绪异象。摘要说该溢价更像系统性风险补偿，而不是短暂错误定价。alpha 的数值、费用和样本期不在已打开摘要里 | 是因子组合检验，不是 T+1 或涨停的交易策略。不要把 “significant alpha” 写成可交易超额 |
| The “T+1” Trading Rule and Put-Call Disparity in China | https://ideas.repec.org/a/wly/jfutmk/v45y2025i12p2314-2331.html | Journal of Futures Markets，2025，45(12): 2314–2331。作者 Hongyi Yang、Zhiyu Chen、Xinying Zhang、Yun Xu | 论文 | 摘要未写期权或 ETF 的样本期。比较对象是受 T+1 约束的股票现货与不受 T+1 约束的期权 | 合成 ETF 的 put-call disparity 在开盘显著高于收盘，并在日内下降；开收差与波动、投机交易和套利限制高度相关。作者还看了合成与标的 ETF 的隔夜/日内收益以及股指期货基差 | 否。没有股票交易策略 |
| Day trading and illiquidity premia: Evidence from China | https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3838211 | SSRN 写 Date Written 2025-12-26，检索摘录写 Last revised 2026-02-03；标注 Pacific-Basin Finance Journal forthcoming。作者 Chaoqun Ma、Ru Xiao、Dominik Rösch | 论文 | 摘要写 1993–2014。处理是 1995 年的日内回转禁令；对照是不受影响的中国 B 股和倾向得分匹配的日本股票 | 禁令后日内收益上升（尤其原先流动性好的股票）、隔夜收益下降、24 小时收益不变。作者排除风险解释，并写时间序列非流动性溢价从夜间移到白天，截面非流动性溢价变小 | 否。是双重差分和收益分解 |
| EvoMarket: A High-Fidelity and Scalable Financial Market Simulator | https://arxiv.org/abs/2604.18046 | 投稿 2026-04-20。作者 Muyao Zhong、Zhenhua Yang、Yuxiang Liu、Ke Tang、Peng Yang | 论文 | 中国 A 股订单流与限价订单簿；摘要写五个交易日的回放对齐，以及开盘集合竞价、涨跌停和 T+1 交收被写进模拟器 | 报告的是模拟器对历史订单簿的重放误差、校准后的深度拟合和计算扩展性，以及跨资产与事件干预实验。没有策略收益 | 否 |
| Generative Models for Financial Time Series Data: Enhancing Signal-to-Noise Ratio and Addressing Data Scarcity in A-Share Market | https://arxiv.org/abs/2501.00063 | 投稿 2024-12-29。作者 Guangming Che | 论文 | 摘要写实验覆盖主板、科创板、创业板、北交所以及 NASDAQ、NYSE、AMEX。没有写起止日期、股票数或费用 | 两种生成模型分别用于提高板块数据信噪比，以及为上市时间短的股票合成变长序列。摘要称合成数据提高了预测模型表现，并提高了个股在 “price trading strategies” 中的信噪比。没有给出收益、回撤、成本或样本外窗口 | 摘要声称触及价格交易策略，但没有可引用的策略结果。不能当成回测证据 |
| R&D-Agent-Quant | https://arxiv.org/html/2505.15155v2 | v2 日期 2025-09-25；NeurIPS 2025 | 论文 / 机构 | CSI300：2008–2014 训练，2015–2016 验证，2017-01-01 至 2020-08-01 测试。另有 CSI500，测试 2024 至 2025-06 | 日频多空优于文中的因子库和深度时序模型；见上一节的 IC 与 ARR。与涨跌停、股吧情绪、集合竞价无关 | 是，日频多空；不是本题的三类策略 |

韩国市场论文未纳入。arXiv 查询 `China A-share price limit`、日期过滤 2024-01-01 至 2026-10-13，返回 6 条，其中与本题直接相关并已打开摘要的是 2604.18046 和 2501.00063。2015/2018 的涨跌停 arXiv（如 https://arxiv.org/html/1503.03548v1 ，样本为 2000–2011 的沪深 A 股高频）出现在结果里，但早于本次窗口，不用它的“收盘封板后次日开盘卖出”说法。

### Inferences

- 这组论文支持的是机制，不是打板手册：涨跌停命中有短期可分类性；开盘集合竞价会吸收股吧隔夜情绪，但卖空约束使买卖两侧不对称；T+1 把收益或非流动性从隔夜推到日内，或体现在期权平价缺口上。
- “散户情绪”在已打开论文里不是一个指数。FRL 用融资相对融券，JBEF 和 QFE 用股吧帖子，三者的样本和市场结果不能相加。
- EvoMarket 说明 2026 年已有人把集合竞价、涨跌停和 T+1 写进 A 股订单簿模拟器，但五个交易日的重放不是策略样本外测试。

### Gaps

- FRL、PBFJ 的 TIAM 论文、Journal of Forecasting、Journal of Futures Markets 的摘要或 IDEAS 页都没有写出样本起止年。付费墙后的表格未打开，所以不补年份、不补 alpha 数值。
- QFE 的 1,100 万帖没有在摘要里写论坛名称和日期范围。不要把它写成东方财富股吧，除非另有页面。JBEF 明确写了 GUBA。
- Pacific-Basin 2025 年 4 月的高频流动性论文（DOI 10.1016/j.pacfin.2025.102681）只在检索摘要中看到 W 形日内流动性和午休，作者与样本年未打开，不列入上表。
- SSRN 上的 T+1 论文只打开了检索返回的摘要，没有 PDF。

## 查不到或打不开的项目/论文

### Takeaway

BaoStock 文档第二次打开失败；tushare.pro 未登录；Qlib 基准 yaml 没有渲染出正文；若干 2024–2026 打板和 A 股 TradingAgents 分支只停留在检索摘要。

### Cited Findings

- BaoStock K 线页 https://baostock.com/mainContent?file=stockKData.md 在 2026-10-13 的直接打开结果只有 “×”。第一次检索返回了复权与停牌段落，故上表保留并标明来源层级。官方复权因子 PDF 的检索链接没有再打开。
- https://tushare.pro 只被旧 GitHub README 指向。本次不登录，因此没有 Pro 的积分、复权、停牌或 PIT 字段说明。
- Qlib 基准配置 https://github.com/microsoft/qlib/blob/main/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml 未渲染出 `open_cost` 等字段。`raw.githubusercontent.com` 与 `export.arxiv.org` 的抓取被环境拦截，所以没有用原始文件补费率。
- 未全文打开、不采用其收益或胜率的仓库：`Fidingks/Mao-Quant`、`sqyangx/quantlab`、`68110923/xunlongjue_doc`、`cy-Yin/TradingAgents-CN-lite`、`simonlin1212/TradingAgents-astock`、`yang1729346/TradingAgents-A-Share`、`touhoufan2024/qlibAssistant`、`xu-duqing/investment_data`、`xu-duqing/deep-alpha`、`vnpy/vnpy_tushare`、`akfamily/akquant`。
- 未打开全文的论文：FRL 108292、PBFJ 102939、Journal of Forecasting 10.1002/for.3197、Journal of Futures Markets 2025 45(12)、SSRN 3838211 的 PDF。JBEF 打开到方法节的样本描述，回归系数表未打开。
- arXiv 日期过滤没有挡住 2016 和 2018 年旧文。2024–2026 的 “China A-share price limit” 查询没有再找到另一篇以涨跌停制度为主题、且能打开摘要的新论文。

### Inferences

- 缺的是可复核的策略结果，不是缺项目名字。检索页上的打板胜率和年化收益在未打开仓库、未看到成本和样本外窗口之前不能用。

### Gaps

- 没有 star、fork、clone 或下载数据包，因此不能核实 chenditc 当前 release 的实际截止日期，也不能重跑 RD-Agent 或 Qlib 基准。
- TradingAgents-CN 的 `pro/main` README 在检索摘录里写过 v0.1.3（2025-06-28）“A股市场完整支持”。该文件没有单独打开，社区版 v3.0 页面也没有这个日期，所以不用它给社区版标注日历日期。
