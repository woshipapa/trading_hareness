# 0015 通达信数据可以作为决策证据，并接入生产采集

- 状态：生效（2026-10-12 操作者决定）；实现见 `.claude/plan/tdx-live-snapshot-fallback.md` 对应的提交
- 日期：2026-10-12

## 背景

通达信集成（`tdx_public`、`tdx_mac`、`derived_tdx_limits`）进入目录时，40 个绑定的 `decision_eligible`
一律是 `False`，升级工具 `scripts/tdx-promote.py` 也写明“不碰 `decision_eligible`”。这是集成阶段的保守约定，
不是目录本身的规则。目录的规则只有一条（`quant-service/app/datasources/catalog.py` 的校验）：
`decision_eligible` 的绑定必须是 `live_verified`。

到 2026-10-12：

- 33 个通达信绑定是 `declared`（owner 出口逐条检查通过），2 个分钟线绑定还在等对账工具修正后重查。
- 盘中两次（10:48、14:53）从 owner 出口把 35 个绑定各调用一次，全部返回数据。
- 收盘后在 owner 上各取一份扶摇和通达信的全 A 快照逐字段比较
  （`scripts/data/tdx_fuyao_all_a_snapshot_agreement_2026-10-12_owner.json`）：5,560 只共同股票的最新价、涨跌幅、
  开盘、最高、最低、昨收、涨跌额完全一致，成交量差在 100 股以内，成交额差在 632 元以内。
- 没有验证过的：盘中两个源在同一时刻的差异（两者不是同一瞬间的快照）。

## 决定

1. **取消“通达信一律不可用于决策”的约定。** 通达信绑定和其他来源一样，按目录规则取得决策资格：
   先到 `live_verified`（需要交易时段内的检查证据），再由操作者同意把 `decision_eligible` 置为 `True`。
   不为通达信另设更宽或更严的规则。
2. **通达信接入生产采集。** 第一处是盘中全 A 快照：扶摇读取失败的那一分钟由通达信补。补的数据如实标注来源，
   不冒用扶摇的标签。
3. **补的数据是否计入决策证据，取决于该绑定当时的 `decision_eligible`。** 也就是说接入可以先上线：
   绑定还是 `declared` 时，通达信补的分钟只进研究展示（分钟截面、雷达、分时温度），信号路径对它按不合格处理；
   绑定升到 `live_verified` 并置为可决策后，同一条链路上的数据自动成为合格证据，不需要再改代码。
4. `tdx_ext`（扩展市场）绑定不在此列：它们的说明写的是“A 股决策路径不得使用”，原因是市场不同，与通达信无关。

## 做法

- 目录给 `tdx_public quote.all_a_snapshot` 声明以扶摇快照为参照的比对，这样交易时段内的升级检查会量出盘中差异。
- 盘中先以影子采集接入：owner 的 `intraday_edge` quant-research 服务每分钟独立读取并保存 `tdx_public/a_share_minute_cross_section_shadow`，再保存与扶摇最近分钟的 `local_derived/all_a_snapshot_source_agreement`；这两条流只作研究测量，不被现有消费者读取。
- 升级工具在 `declared → live_verified` 这一步可以带上本决策编号，把 `decision_eligible` 一并置为 `True`；
  不带就不动。
- 下一个交易时段内对 `tdx_public quote.all_a_snapshot` 跑检查，通过后升级并置为可决策，随后发布。

## 影响

- 扶摇被拒或超时不再等于丢一分钟的全市场截面。
- 升级为可决策之后，通达信的价格和由它派生的量比、换手会进入信号规则接受的证据集合；在那之前不会。
- 研究平台的其余约束不变：`live_effect`、晋级记录、缺数据时按失败处理。
