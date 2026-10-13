// Pure helpers for the session radar tab: the minute market radar, the
// strategy cards and the limit-up detail served by quant-research.
//
// The radar reverse-engineers a vendor's 情绪·系统雷达: the stocks that touched
// +x% (or -x%) at any point in the day stay on that side, and their turnover
// so far is summed. The middle band is the pool minus both sides. The
// "now" readings follow the price instead. Turnover is in CNY and shown in 亿.

export type Cell = { turnover: number; count: number };
export type Breadth = { up: number; down: number; flat: number };
export type BandCells = Record<'cum_up' | 'cum_down' | 'middle' | 'now_up' | 'now_down' | 'now_middle', Cell>;
export type RadarPoint = {
  observed_at: string;
  phase: string;
  pool?: Cell;
  bands?: Record<string, BandCells>;
  breadth?: Breadth;
  segments?: Record<string, { pool: Cell; bands: Record<string, BandCells>; breadth?: Breadth }>;
  auction?: { up: Record<string, number>; down: Record<string, number>; priced: number };
};
export type MainNetPoint = {
  observed_at: string; main_net: number; boards: number; taxonomy_key?: string | null;
  source?: string | null; upstream?: string | null;
};
export type RadarDay = {
  trade_date: string; points: RadarPoint[]; main_net: MainNetPoint[]; definitions?: Record<string, string>;
};
export type RadarRow = {
  time: string; observedAt: string; phase: string; cumUp: number | null; cumDown: number | null; middle: number | null; pool: number | null;
  nowUp: number | null; nowDown: number | null; mainNet: number | null;
  counts: { cumUp: number | null; cumDown: number | null; nowUp: number | null; nowDown: number | null }; breadth: Breadth | null;
  auction?: { up: number | null; down: number | null; priced: number };
};

export const SEGMENT_LABELS: Record<string, string> = {
  all: '全市场', sh_main: '沪主板', sz_main: '深主板', chinext: '创业板', star: '科创板', beijing: '北交所',
};
export const BAND_LABELS: Record<string, string> = { '2': '±2%', '5': '±5%', limit: '涨跌停' };
export const PHASE_LABELS: Record<string, string> = {
  auction_cancellable: '竞价·可撤单', auction_locked: '竞价·不可撤', auction_matched: '竞价撮合后',
  continuous: '连续竞价', closing_auction: '收盘集合竞价', outside: '非交易时段',
};
const YI = 100_000_000;

export function yi(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '-';
  return `${(value / YI).toFixed(digits)}亿`;
}

export function shanghaiTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.valueOf())) return iso;
  return date.toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false, hour: '2-digit', minute: '2-digit' });
}

export function shanghaiToday(now: Date = new Date()): string {
  return now.toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' });
}

function cellsOf(point: RadarPoint, band: string, segment: string): { pool: Cell; bands: BandCells; breadth: Breadth | null } | null {
  if (segment === 'all') {
    const bands = point.bands?.[band];
    return point.pool && bands ? { pool: point.pool, bands, breadth: point.breadth ?? null } : null;
  }
  const part = point.segments?.[segment];
  const bands = part?.bands?.[band];
  return part && bands ? { pool: part.pool, bands, breadth: part.breadth ?? null } : null;
}

/** The main net as of a moment: the latest flow snapshot not after it. */
function mainNetAsOf(flows: MainNetPoint[], observedAt: string): number | null {
  const at = Date.parse(observedAt);
  let value: number | null = null;
  for (const flow of flows) {
    if (Date.parse(flow.observed_at) > at) break;
    value = flow.main_net;
  }
  return value;
}

export function radarRows(day: RadarDay | null | undefined, band: string, segment: string): RadarRow[] {
  if (!day) return [];
  const flows = [...(day.main_net ?? [])].sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at));
  const rows: RadarRow[] = [];
  for (const point of day.points ?? []) {
    const cells = cellsOf(point, band, segment);
    if (!cells && !point.auction) continue;
    rows.push({
      time: shanghaiTime(point.observed_at), observedAt: point.observed_at, phase: point.phase,
      cumUp: cells?.bands.cum_up.turnover ?? null, cumDown: cells?.bands.cum_down.turnover ?? null,
      middle: cells?.bands.middle.turnover ?? null, pool: cells?.pool.turnover ?? null,
      nowUp: cells?.bands.now_up.turnover ?? null, nowDown: cells?.bands.now_down.turnover ?? null,
      // The board flow is market-wide; it is not split by segment.
      mainNet: cells && segment === 'all' ? mainNetAsOf(flows, point.observed_at) : null,
      counts: {
        cumUp: cells?.bands.cum_up.count ?? null, cumDown: cells?.bands.cum_down.count ?? null,
        nowUp: cells?.bands.now_up.count ?? null, nowDown: cells?.bands.now_down.count ?? null,
      },
      breadth: cells?.breadth ?? null,
      auction: point.auction ? {
        up: point.auction.up[band] ?? null,
        down: point.auction.down[band] ?? null,
        priced: point.auction.priced,
      } : undefined,
    });
  }
  return rows;
}

export type AuctionRow = { time: string; phase: string; up2: number; up5: number; down2: number; down5: number; priced: number };

export function auctionRows(day: RadarDay | null | undefined): AuctionRow[] {
  return (day?.points ?? []).filter((point) => point.auction).map((point) => ({
    time: shanghaiTime(point.observed_at), phase: PHASE_LABELS[point.phase] ?? point.phase,
    up2: point.auction!.up['2'] ?? 0, up5: point.auction!.up['5'] ?? 0,
    down2: point.auction!.down['2'] ?? 0, down5: point.auction!.down['5'] ?? 0, priced: point.auction!.priced,
  }));
}

const UP = '#d93026';
const DOWN = '#188038';
const AMBER = '#b7791f';

export function displayRows(rows: RadarRow[]): RadarRow[] {
  const sorted = rows.filter((row) => row.time >= '09:15').slice().sort((a, b) => Date.parse(a.observedAt) - Date.parse(b.observedAt));
  const buckets = new Map<number, RadarRow>();
  for (const row of sorted) {
    const [hour, minute] = row.time.split(':').map(Number);
    const bucket = Math.floor((hour * 60 + minute - (9 * 60 + 15)) / 2);
    buckets.set(bucket, row);
  }
  const kept = [...buckets.values()];
  const match = sorted.find((row) => row.phase === 'auction_matched' || row.time === '09:25');
  const newest = sorted[sorted.length - 1];
  for (const row of [match, newest]) {
    if (row && !kept.some((item) => item.observedAt === row.observedAt)) kept.push(row);
  }
  return kept.sort((a, b) => Date.parse(a.observedAt) - Date.parse(b.observedAt));
}

export type RadarHeadline = {
  cumDown: number | null; cumUp: number | null; downDelta: number | null; upDelta: number | null;
  auctionDown: number | null; auctionUp: number | null;
};

export function radarHeadline(rows: RadarRow[]): RadarHeadline {
  const displayed = displayRows(rows);
  const latest = displayed[displayed.length - 1];
  const previous = displayed[displayed.length - 2];
  const delta = (current: number | null, prior: number | null) => current === null || prior === null ? null : current - prior;
  const auction = rows.find((row) => row.phase === 'auction_matched' || row.time === '09:25');
  return {
    cumDown: latest?.cumDown ?? null, cumUp: latest?.cumUp ?? null,
    downDelta: delta(latest?.cumDown ?? null, previous?.cumDown ?? null),
    upDelta: delta(latest?.cumUp ?? null, previous?.cumUp ?? null),
    auctionDown: auction?.cumDown ?? null, auctionUp: auction?.cumUp ?? null,
  };
}

export function radarTooltip(row: RadarRow, bandLabel: string): string[] {
  const lines = [row.time];
  if (row.cumDown === null) {
    const auction = row.auction;
    return [...lines, `竞价虚拟价：高于 +${bandLabel.replace(/^±/, '')} ${auction?.up ?? '-'} 只 · 低于 -${bandLabel.replace(/^±/, '')} ${auction?.down ?? '-'} 只（已有价格 ${auction?.priced ?? '-'} 只）`];
  }
  lines.push(`主动卖 ${yi(row.cumDown)}（即时 ${yi(row.nowDown)}）`);
  lines.push(`主动买 ${yi(row.cumUp)}（即时 ${yi(row.nowUp)}）`);
  lines.push(`中间带（${bandLabel} 内） ${yi(row.middle)}`);
  lines.push(`全池总额 ${yi(row.pool)}`);
  lines.push(`主力净额 ${yi(row.mainNet)}`);
  lines.push(`卖/买 = 当日曾触及 ${bandLabel} 的股票累计成交额（只进不出）；即时 = 此刻仍在 ${bandLabel} 之外`);
  return lines;
}

export function radarOption(rows: RadarRow[], bandLabel: string): Record<string, unknown> {
  const displayed = displayRows(rows);
  const line = (name: string, values: (number | null)[], color: string, dashed = false) => ({
    name, type: 'line', showSymbol: true, symbolSize: 4, smooth: false, data: values.map((v) => (v === null ? null : +(v / YI).toFixed(2))),
    lineStyle: { color, width: dashed ? 1 : 2, type: dashed ? 'dashed' : 'solid' }, itemStyle: { color },
  });
  const byTime = new Map(displayed.map((row) => [row.time, row]));
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      formatter: (params: Array<{ axisValue: string }>) => {
        const row = byTime.get(params[0]?.axisValue ?? '');
        return row ? radarTooltip(row, bandLabel).join('<br/>') : '';
      },
    },
    legend: { top: 0 },
    grid: { left: 56, right: 64, top: 36, bottom: 40 },
    xAxis: { type: 'category', data: displayed.map((row) => row.time) },
    yAxis: [
      { type: 'value', name: '成交额(亿)', scale: true },
      { type: 'value', name: '主力净额(亿)', scale: true, splitLine: { show: false } },
    ],
    dataZoom: [{ type: 'inside' }],
    series: [
      { ...line('主动卖（累计）', displayed.map((row) => row.cumDown), DOWN), markLine: {
        silent: true, symbol: 'none', data: [
          { xAxis: '09:25', label: { formatter: '9:25 定格', color: DOWN }, lineStyle: { color: DOWN, type: 'dashed' } },
          { xAxis: '09:30', label: { formatter: '9:30', color: '#666' }, lineStyle: { color: '#999', type: 'dashed' } },
        ],
      } },
      line('主动买（累计）', displayed.map((row) => row.cumUp), UP),
      line('中间带', displayed.map((row) => row.middle), '#8a8f98'),
      {
        name: '主力净额', type: 'line', yAxisIndex: 1, showSymbol: false,
        data: displayed.map((row) => row.mainNet === null ? null : +(row.mainNet / YI).toFixed(2)),
        lineStyle: { color: AMBER, width: 2, type: 'dashed' }, itemStyle: { color: AMBER },
      },
    ],
  };
}

export function pctText(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '-';
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`;
}

export function tone(value: number | null | undefined): 'up' | 'down' | 'flat' {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) return 'flat';
  return value > 0 ? 'up' : 'down';
}

export const GATE_LABELS: Record<string, string> = { up: '偏多', down: '偏空', mixed: '震荡', unknown: '未知' };

export function stars(count: number | null | undefined): string {
  const n = Math.max(0, Math.min(5, count ?? 0));
  return n ? '★'.repeat(n) : '—';
}

export type CardPick = {
  symbol: string; name?: string | null; rank?: number | null; score?: number | null; score_scale?: string | null;
  auction?: { pct_change?: number | null; turnover?: number | null } | null;
  open_pct?: number | null; latest_pct?: number | null; since_open_pct?: number | null; price?: number | null;
  at_limit_up?: boolean; at_limit_down?: boolean; opened_at_limit_up?: boolean;
  ticket?: boolean; entry_signals?: { model: string; state: string; observed_at: string }[];
  themes?: { code: string; label: string; limit_up_members: number }[];
  limit_review?: { theme?: string | null; reason?: string | null; board_count?: number | null } | null;
  events?: { kind: string; label: string }[]; event_count?: number;
  resonance?: number; resonance_lines?: string[];
};
export type LeaderboardCell = {
  days: number; picks: number; scored: number; mean_open_to_close_pct: number | null; rose_share: number | null;
  opened_at_limit: number; without_bar: number;
};
export type StrategyCard = {
  strategy_key: string; name: string; style: string; direction: 'long' | 'short'; contract_key?: string | null;
  model_version?: string | null; maturity: string; candidates: number;
  summary: { picks: number; quoted: number; scored: number; mean_since_open_pct: number | null; rose_share: number | null;
             tickets: number; best?: { symbol: string; name?: string | null; since_open_pct: number } | null };
  picks: CardPick[]; leaderboard: Record<string, LeaderboardCell>;
};
export type DirectionGate = {
  label: string; up_down_ratio?: number | null; main_net?: number | null; main_net_source?: string | null;
  main_net_upstream?: string | null; radar_observed_at?: string | null; phase?: string | null; definition?: string;
};
export type StrategyCardsDay = {
  trade_date: string; status: string; reason?: string; picks_as_of?: string; direction_gate?: DirectionGate;
  previous_close_regime?: { regime_label?: string | null } | null; cards: StrategyCard[];
  data_health?: { all_eligible: boolean; fields: Record<string, { indicator: string; status: string; decision_eligible: boolean }> };
  leaderboard?: Record<string, ({ strategy_key: string; name: string } & LeaderboardCell)[]>;
  definitions?: Record<string, string>;
};
export type LimitStock = {
  symbol: string; name?: string | null; first_limit_up_at?: string | null; last_limit_up_at?: string | null;
  break_times?: number | null; one_word?: boolean | null; board_count?: number | null; board_label?: string | null;
  m_days?: number | null; n_boards?: number | null; seal_amount?: number | null; main_net?: number | null;
  turnover?: number | null; theme?: string | null; reason?: string | null; sources: string[];
  agreement?: { first_seal_gap_seconds?: number | null; board_count_equal?: boolean | null };
};
export type LimitDetailDay = {
  trade_date: string; status: string; stocks: LimitStock[];
  coverage?: Record<string, number>; broken?: unknown[]; limit_down?: unknown[];
};

export function lineRankText(cell: LeaderboardCell | undefined): string {
  if (!cell || cell.mean_open_to_close_pct === null) return '-';
  const share = cell.rose_share === null ? '' : ` · 涨${Math.round(cell.rose_share * 100)}%`;
  return `${pctText(cell.mean_open_to_close_pct)}${share}（${cell.scored}/${cell.picks}）`;
}

export function boardText(stock: LimitStock): string {
  if (stock.m_days && stock.n_boards && stock.m_days !== stock.n_boards) return `${stock.m_days}天${stock.n_boards}板`;
  return stock.board_label || (stock.board_count ? `${stock.board_count}板` : '-');
}

export type IndicatorCheck = { name: string; status: string; value?: unknown; threshold?: unknown; detail?: string };
export type IndicatorStatus = {
  key: string; label: string; availability: string; status: string; decision_eligible: boolean; checks: IndicatorCheck[];
};
export type IndicatorHealth = {
  trade_date: string; observed_at: string; indicators: IndicatorStatus[]; summary: Record<string, number>;
  decision_eligible_keys: string[]; rule?: string;
};
export const HEALTH_LABELS: Record<string, string> = { ok: '可用', warn: '警告', fail: '失败', missing: '缺失', pending: '未到时' };
export const HEALTH_TYPES: Record<string, string> = { ok: 'success', warn: 'warning', fail: 'danger', missing: 'danger', pending: 'info' };

/** The checks that withhold eligibility, as one line. */
export function failingChecks(item: IndicatorStatus): string {
  return item.checks.filter((check) => check.status !== 'ok')
    .map((check) => `${check.name}=${check.value ?? '-'}（${check.detail || check.threshold || check.status}）`).join('；');
}

/** Each card field with its indicator's health, as one line. */
export function fieldHealthText(day: StrategyCardsDay | null | undefined): string {
  const fields = day?.data_health?.fields ?? {};
  return Object.entries(fields)
    .map(([field, item]) => `${field}：${item.indicator} ${HEALTH_LABELS[item.status] ?? item.status}`).join('；');
}
