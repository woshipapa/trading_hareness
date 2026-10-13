# Readable 主动买/卖·主力净额 chart in the session radar tab

Status: 2026-10-13. Requested by the user with a reference screenshot ("现在前端的看不懂"). Frontend only.
Code facts below were mapped on 2026-10-13 (file:line from the survey); verify before editing.

## Goal

The session radar tab already receives everything the reference chart shows; only the presentation is unreadable
(series named `曾≥+±2%累计`, six lines and a bar chart, no headline, no auction marker). Redraw it so a reader
gets the day's answer at a glance: how much turnover sits in stocks that fell, how much in stocks that rose, what
the auction looked like, and the main net.

Nothing in the backend changes. Research display only.

## What exists

- Data: `GET /api/research/market/radar?trade_date=...` (relay → `/api/v1/market/radar`). `points[]`: one a minute,
  `phase`, `pool{turnover,count}`, `bands["2"|"5"|"limit"]` each with `cum_up`, `cum_down`, `middle`, `now_up`,
  `now_down` (`{turnover,count}`), `breadth`; points in the auction phases before 09:25 carry only
  `auction{up{"2","5"},down{"2","5"},priced}` (counts of stocks by indicative price, no turnover). `main_net[]`:
  `observed_at`, `main_net`. Turnover in CNY.
- A stock that touched +2% (or -2%) at any time in the day stays on that side for the rest of the day (`cum_*`);
  `now_*` follows the current price.
- `frontend/src/research/session-radar.ts`: `radarRows` (rows with `cumUp`, `cumDown`, `middle`, `pool`, `nowUp`,
  `nowDown`, `mainNet`, `counts`), `auctionRows`, `radarLegend`, `radarOption(rows, bandLabel)` (the chart),
  tests in `session-radar.test.ts`. `frontend/src/views/research/SessionRadarTab.vue` renders it with vue-echarts
  and polls every 60 s.

## The chart

Title row: **主动买/卖·主力净额**, and a one-line legend of meaning in small text:
`2 分钟一点 · 9:25 为竞价撮合 · 主动卖 = 当日曾跌超 {band} 的股票成交额 · 主动买 = 当日曾涨超 {band} 的股票成交额`.

Headline block, left of the plot, large numbers, green for sell and red for buy (A-share convention):
- `卖 {cumDown}亿` and `买 {cumUp}亿` at the latest point;
- under them, small: `近 2 分钟 卖 +{Δ}亿 · 买 +{Δ}亿` (difference between the last two displayed points);
- `竞买 {x}亿 · 竞砸 {y}亿`: `cum_up` and `cum_down` turnover of the first point whose phase is `auction_matched`
  (the 09:25 match); absent before 09:25.

Series:
- 主动卖（累计）: green solid line with a dot per displayed point;
- 主动买（累计）: red solid line with a dot per displayed point;
- 中间带: grey line;
- 主力净额: amber dashed line on the right axis (a line, not bars).
`pool`, `nowUp` and `nowDown` are not drawn; they appear in the tooltip.

Time axis: one displayed point per 2-minute bucket (the last minute point of the bucket), always keeping the
09:25 match point and the newest point. A vertical marker at the 09:25 point labelled `9:25 定格` and one at 09:30.
Points before 09:25 have no turnover: the lines start at the match; the tooltip for those points shows the
indicative counts instead.

Tooltip (custom formatter, one block per point):
```
09:35
主动卖 790.9亿（即时 834.0亿）
主动买 104.5亿（即时 102.0亿）
中间带（±2% 内） 466.2亿
全池总额 1361.6亿
主力净额 -54.6亿
卖/买 = 当日曾触及 ±2% 的股票累计成交额（只进不出）；即时 = 此刻仍在 ±2% 之外
```
and before 09:25: `竞价虚拟价：高于 +2% {n} 只 · 低于 -2% {m} 只（已有价格 {priced} 只）`.

The band selector (±2%, ±5%, 涨跌停) and the segment selector keep working; the labels use the selected band
without the stray `±` that produced `曾≥+±2%累计`.

## Steps (one dispatch)

1. `session-radar.ts`: `displayRows(rows)` (2-minute bucketing with the two kept points), `radarHeadline(rows)`
   (latest sell and buy, the 2-minute differences, the auction pair), `radarTooltip(row, bandLabel)` (the text
   block as an array of lines), and `radarOption` rewritten to the series, markers and tooltip above. Remove the
   helpers that are no longer used.
2. `SessionRadarTab.vue`: title row, headline block and the chart; nothing else in the tab changes.
3. `session-radar.test.ts`: bucketing (keeps 09:25 and the newest point, one point per 2 minutes otherwise),
   headline numbers and differences, the auction pair from the first `auction_matched` point, tooltip lines for
   a continuous point and for a pre-match point, series names without `±` duplication, the main net on the
   right axis as a line.

## Acceptance

```bash
cd frontend && npm run typecheck && npm run test -- --run && npm run build
python3 -m pytest scripts/test_relay_research_routes.py -q
git diff --check
```
Then deploy the console and look at the page with today's data.

## Out of scope

- Any backend change (auction-phase turnover before 09:25 does not exist in the radar and is not added here).
- The picks-and-avoid board (separate plan).
