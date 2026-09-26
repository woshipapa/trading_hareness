# Longhu 能力接入与 shadow 策略契约

状态：`research_only`，`live_effect=none`。

## 能力边界

当前 owner shared gateway 登记 6 个 Longhu 目标、50 个 documented
operations、89 个参数样例。应用层按唯一 `target + action` 生成 51 条能力契约，
通过 `/api/v1/research/longhu/catalog` 的 `integration_contracts` 返回。

每条能力都保留 `target`、`action`、`controller`、可用时钟、证据数据集和策略角色。
这份目录只描述接入边界，不把“网关可调用”误认为“已验证可用于实时决策”。

## 当前角色

- `live_watch`：`GetStockPanKou`、`GetStockTrendIncremental`。仍需交换日、时间戳和新鲜度门禁。
- `candidate_confirmation`：`GetStockDaDanTrendIncremental`。只用于候选确认研究，不改变盘中阈值。
- `post_close_research`：竞价、市场情绪、涨跌停表现、新闻和主题等盘后证据。
- `shadow_research`：筹码、竞价委托、板块竞价、市场雷达、热点和机构/股东接口。
- `historical_research`：历史日线、历史涨跌统计和历史复盘接口。

所有角色都保留 raw payload；未完成字段字典、单位和点时验证前，不得提升为 live
策略因子。

## Longhu 多因子 shadow v1

`app/longhu_multifactor_shadow.py` 提供一个纯函数评分器，输入为已经完成来源
归一化的七类因子：

- 实时涨幅强度
- 分钟动量
- 量能确认
- 委托簿不平衡
- 大单净流
- 板块相对强度
- 竞价强度

评分器不会猜测供应商字段单位；缺失或过期证据会降低覆盖率并阻断候选。输出
固定带有 `research_only=true`、`live_effect=none` 和
`promotion_record_required=true`。它已登记为
`longhu_multifactor_shadow / longhu-multifactor-shadow-v1`，但没有接管当前
盘中扫描、提醒阈值或任何订单路径。

晋级前必须完成：同日来源时钟校验、字段/单位字典、至少 20 个交易日 forward
shadow、至少 200 个独立成熟事件、样本外评估和人工 promotion record。
