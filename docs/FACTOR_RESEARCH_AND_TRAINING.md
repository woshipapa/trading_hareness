# 因子研究、回测与训练路线

## 已实现边界

量化服务提供受版本控制的因子注册表、点时因子评估和 A 股约束回测。它们读取
`canonical_bars_daily`、`daily_adjustment_factors`、`daily_fundamentals` 的点时证据；
owner 完成全部五张冷表、schema、复权 data guard 后，会对需要的关系构造原子
hot+cold union，任何部分 cutover 都只读 hot。结果写入 `quant.factor_evaluations`
和 `quant.strategy_experiments`。这是一条研究链路，不会下单，也不会自动把实验结果
升级为推荐模型。

所有跨 session 价格和收益使用 `raw price × positive cumulative factor`。因子必须来自 owner 的 `longhu_qfq_derived`，或明确允许的 Tushare checkpoint provider；同日恒等占位值、未知来源和缺失因子一律阻断。
涨跌停、停牌和可成交性仍以交易所 raw price 判断；缺因子或非 complete 语义时不回退
到 raw 收益，而是 fail-closed。

当前原生因子覆盖动量、反转、均线偏离、波动率、量比、日内收盘强度和东财资金流。
评估输出 Rank IC、ICIR、正 IC 比例、多空分层收益和顶部股票池换手率。少于 20 个
横截面日或 50 个观测值的结果固定标记为 `insufficient_history`。

回测模型只做多，并在信号日收盘后以下一交易日开盘价（缺失时收盘价）入场；停牌、
涨停买入和跌停卖出都阻断交易，成本以请求中的单边 bps 显式传入。它是保守研究假设，
不是经纪商成交模拟器。

## 离线基线 worker

`python -m app.research_model_training_worker` 现在是一个真实的一次性训练任务，而不是
HTTP 进程中的占位调用。它只读取已落库的点时 `all_a` 日频证据，从哈希固定的 64 个
样本标的生成 5 日方向标签与 7 个基础特征，要求研究窗口内至少 720 个完整横截面日，
再执行 60 日训练、5 日 embargo、20 日测试的滚动 OOF。数据表、模型、manifest、代码
文件摘要和每个预登记 trial 都绑定 SHA-256；trial 写入
`quant.research_model_trials`，候选写入 `quant.research_model_registry`。

本地 2026-09-18 的首个真实运行包含 42,592 个样本、703 个可训练交易日和 3 个 L2
正则 trial。选中 trial 的 OOF ROC AUC 为 0.5164，但 log-loss 0.7094，差于常数基线
0.7026，因此注册状态为 `rejected`，`approved_at` 为空且 `live_effect=none`。这个负结果
是训练闭环工作的验收证据，不是策略有效性的证据。确定性样本是资源有界的 CPU 基线，
不能冒充完整 Alpha158、LightGBM 或跨框架 benchmark。

## 外部框架适配

`GET /api/v1/research-frameworks` 现在会在不导入或执行外部框架的前提下附加
`readiness` 投影。它区分目录中的 `adapter_ready`、当前进程是否能发现可选依赖、
数据门槛是否已经由独立证据核验以及实际 benchmark 是否执行；依赖存在也不会把框架标成
已验证。所有投影都带 `research_only=true` 和 `live_effect=none`，缺依赖、数据门槛未核验
或未执行 benchmark 时保持 `blocked`/`planned`，不会写入阈值、推荐权重或订单路径。

- AlphaLens-ReLoaded：本地评估结果已对齐 Rank IC、分层收益和换手的概念；完整图表
  适配应在独立研究 worker 对导出的因子与远期收益运行。
- Microsoft Qlib：输出契约预留给 Alpha158/Alpha360 风格数据集、训练与工作流。Qlib
  只能在拥有点时全市场历史、退市标的和滚动验证后运行。
- QuantConnect LEAN：作为独立严格执行回测验证器，必须先实现 A 股交易规则和数据桥。
- FinRL / FinRL-X：仅作为 GPU 离线研究，不能替代传统因子基线或直接产出线上交易指令。
- VectorBT：适合隔离实验环境的参数扫描；由于许可边界，不作为核心生产依赖。

## H100 路线

1. CPU：补齐至少三年点时日线、复权、财报披露、退市与行业历史；建立滚动样本外标签。
2. CPU：筛选基础因子并通过 IC、换手、交易成本、回撤和不同市场状态门槛。
3. 1 张 H100 80GB：训练 LightGBM/时序监督模型并与多因子基线做滚动比较。
4. 4 张 H100 80GB：进行特征组合、超参扫描和市场状态压力测试。
5. 4-8 张 H100 80GB：在已验证的 A 股约束环境中进行 FinRL 研究；只有 shadow 期和
   样本外门槛通过后，模型才可成为推荐的一个降权输入。

所有训练任务应运行在独立 worker，使用不可变数据快照、容器镜像哈希、代码文件摘要、
随机种子、代码版本和评估产物 URI。当前线上服务只展示注册表，不加载任何训练工件；
未来也只能读取经独立人工晋升且通过全部数据、样本外和纸面门禁的模型。
