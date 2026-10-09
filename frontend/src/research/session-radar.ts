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
  time: string; observedAt: string; cumUp: number; cumDown: number; middle: number; pool: number;
  nowUp: number; nowDown: number; mainNet: number | null;
  counts: { cumUp: number; cumDown: number; nowUp: number; nowDown: number }; breadth: Breadth | null;
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
    if (!cells) continue;
    rows.push({
      time: shanghaiTime(point.observed_at), observedAt: point.observed_at,
      cumUp: cells.bands.cum_up.turnover, cumDown: cells.bands.cum_down.turnover, middle: cells.bands.middle.turnover,
      pool: cells.pool.turnover, nowUp: cells.bands.now_up.turnover, nowDown: cells.bands.now_down.turnover,
      // The board flow is market-wide; it is not split by segment.
      mainNet: segment === 'all' ? mainNetAsOf(flows, point.observed_at) : null,
      counts: {
        cumUp: cells.bands.cum_up.count, cumDown: cells.bands.cum_down.count,
        nowUp: cells.bands.now_up.count, nowDown: cells.bands.now_down.count,
      },
      breadth: cells.breadth,
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

/** Stocks on each side now, ever on each side, and - for the limit band - those that broke. */
export function bandCounts(rows: RadarRow[], band: string): string {
  const last = rows[rows.length - 1];
  if (!last) return '';
  const { cumUp, cumDown, nowUp, nowDown } = last.counts;
  if (band === 'limit') {
    return `涨停 ${nowUp}（曾 ${cumUp}，炸板 ${Math.max(0, cumUp - nowUp)}）· 跌停 ${nowDown}（曾 ${cumDown}）`;
  }
  return `在上方 ${nowUp}/曾 ${cumUp} · 在下方 ${nowDown}/曾 ${cumDown}`;
}

export function breadthText(rows: RadarRow[]): string {
  const breadth = rows[rows.length - 1]?.breadth;
  return breadth ? `上涨 ${breadth.up} · 下跌 ${breadth.down} · 平 ${breadth.flat}` : '';
}

export function radarLegend(rows: RadarRow[]): Record<string, string> {
  const last = rows[rows.length - 1];
  if (!last) return {};
  return {
    cumDown: yi(last.cumDown), cumUp: yi(last.cumUp), middle: yi(last.middle), pool: yi(last.pool),
    nowUp: yi(last.nowUp), nowDown: yi(last.nowDown), mainNet: yi(last.mainNet), time: last.time,
  };
}

const UP = '#d93026';
const DOWN = '#188038';

export function radarOption(rows: RadarRow[], bandLabel: string): Record<string, unknown> {
  const line = (name: string, values: (number | null)[], color: string, dashed = false) => ({
    name, type: 'line', showSymbol: false, smooth: false, data: values.map((v) => (v === null ? null : +(v / YI).toFixed(2))),
    lineStyle: { color, width: dashed ? 1 : 2, type: dashed ? 'dashed' : 'solid' }, itemStyle: { color },
  });
  return {
    animation: false,
    tooltip: { trigger: 'axis', valueFormatter: (value: number) => (value === null || value === undefined ? '-' : `${value}亿`) },
    legend: { top: 0 },
    grid: { left: 56, right: 56, top: 36, bottom: 40 },
    xAxis: { type: 'category', data: rows.map((row) => row.time) },
    yAxis: [
      { type: 'value', name: '成交额(亿)', scale: true },
      { type: 'value', name: '主力净额(亿)', scale: true, splitLine: { show: false } },
    ],
    dataZoom: [{ type: 'inside' }],
    series: [
      line(`曾≥+${bandLabel}累计`, rows.map((row) => row.cumUp), UP),
      line(`曾≤-${bandLabel}累计`, rows.map((row) => row.cumDown), DOWN),
      line('中间带', rows.map((row) => row.middle), '#8a8f98'),
      line('全池', rows.map((row) => row.pool), '#5470c6', true),
      line('此刻在上方', rows.map((row) => row.nowUp), UP, true),
      line('此刻在下方', rows.map((row) => row.nowDown), DOWN, true),
      {
        name: '主力净额', type: 'bar', yAxisIndex: 1, barMaxWidth: 6,
        data: rows.map((row) => (row.mainNet === null ? null : {
          value: +(row.mainNet / YI).toFixed(2), itemStyle: { color: row.mainNet >= 0 ? UP : DOWN },
        })),
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
