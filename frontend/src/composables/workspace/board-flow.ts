import { computed, ref } from 'vue';
import type {
  BoardFlowPoint, BoardFlowResponse, BoardFlowSeries, BoardFlowSnapshot, BoardRotationEvent, BoardStockMining,
  LimitLinkageMining, MarketFlowResponse, SectorFlowDailyFeature,
} from './types';
import { chinaMinute } from './format';
import type { ResearchShell } from './shell';

/**
 * 盘中板块资金 on 收盘复盘: the minute board-flow curves (merged incrementally
 * by cursor), the market flow features, board rotation events and the
 * board-to-stock and limit-linkage mining cards.  The workspace polls these
 * loaders once a minute while the exchange day is live.
 */
export function useBoardFlowSlice(shell: ResearchShell) {
  const { getJson } = shell.transport;

  const boardFlowTaxonomy = ref<'industry' | 'concept'>('industry');
  const boardFlowDate = ref('');
  const boardFlowSeries = ref<Record<string, BoardFlowSeries>>({});
  const boardFlowSnapshots = ref<BoardFlowSnapshot[]>([]);
  const boardFlowCursor = ref<string | null>(null);
  const boardFlowLoading = ref(false);
  const boardFlowError = ref('');
  const boardFlowNotice = ref('');
  const boardFlowFocus = ref<string[]>([]);
  const boardFlowDisplaySlots = ref<string[]>([]);
  const boardFlowIsExchangeToday = ref(false);
  const boardRotationEvents = ref<BoardRotationEvent[]>([]);
  const boardStockMining = ref<BoardStockMining>({});
  const limitLinkageMining = ref<LimitLinkageMining>({});
  const marketFlow = ref<MarketFlowResponse>({ trade_date: '', timezone: 'Asia/Shanghai', items: [] });
  const marketFlowError = ref('');

  const marketFlowLatest = computed(() => marketFlow.value.latest ?? marketFlow.value.items.at(-1) ?? null);
  const marketFlowSectorHighlights = computed(() => [...(marketFlow.value.sector_daily ?? [])]
    .sort((left, right) => {
      const priority = (row: SectorFlowDailyFeature) => ['reversal_in', 'reversal_out', 'acceleration_in', 'acceleration_out'].includes(row.transition) ? 1 : 0;
      return priority(right) - priority(left)
        || Math.abs(Number(right.net_change_amount ?? 0)) - Math.abs(Number(left.net_change_amount ?? 0))
        || Number(right.lhb_stock_count ?? 0) - Number(left.lhb_stock_count ?? 0);
    }).slice(0, 20));
  const marketFlowStateLabel = (value?: string | null) => ({
    flow_expansion: '资金扩张', flow_risk_off: '资金退潮', late_repair: '尾盘修复',
    flow_acceleration: '流入加速', flow_deterioration: '流入恶化', mixed_rotation: '轮动混合',
    risk_expansion: '放量扩张', distribution: '放量派发', weak_repair: '缩量修复',
    passive_decline: '缩量走弱', neutral_rotation: '中性轮动', insufficient: '数据不足',
  }[value ?? ''] ?? value ?? '等待特征');
  const marketFlowStateType = (value?: string | null): 'success' | 'warning' | 'danger' | 'info' => (
    ['flow_expansion', 'flow_acceleration', 'risk_expansion'].includes(value ?? '') ? 'success'
      : ['flow_risk_off', 'flow_deterioration', 'distribution', 'passive_decline'].includes(value ?? '') ? 'danger'
        : ['late_repair', 'weak_repair', 'mixed_rotation', 'neutral_rotation'].includes(value ?? '') ? 'warning' : 'info'
  );
  const sectorFlowTransitionLabel = (value?: string | null) => ({ reversal_in: '流出转流入', reversal_out: '流入转流出', acceleration_in: '流入加速', acceleration_out: '流出加速', persistent_in: '持续流入', persistent_out: '持续流出', flat: '平稳', insufficient: '证据不足' }[value ?? ''] ?? value ?? '-');
  const sectorFlowTransitionType = (value?: string | null): 'success' | 'warning' | 'danger' | 'info' => value === 'reversal_in' || value === 'acceleration_in' ? 'success' : value === 'reversal_out' || value === 'acceleration_out' ? 'danger' : value === 'persistent_out' ? 'warning' : 'info';
  const marketFlowChartOption = computed(() => {
    const rows = marketFlow.value.items.filter((item) => item.cadence === 'minute');
    return {
      animation: false,
      tooltip: { trigger: 'axis' },
      grid: { left: 58, right: 54, top: 34, bottom: 46 },
      xAxis: { type: 'category', data: rows.map((item) => chinaMinute(item.observed_at)), boundaryGap: false },
      yAxis: [
        { type: 'value', name: '流入广度%', min: 0, max: 100 },
        { type: 'value', name: '资金中位数', scale: true },
      ],
      dataZoom: [{ type: 'inside', filterMode: 'none' }],
      series: [
        { name: '概念流入广度', type: 'line', showSymbol: false, smooth: true, data: rows.map((item) => item.concept_positive_ratio == null ? null : Number(item.concept_positive_ratio) * 100), lineStyle: { color: '#b71c1c', width: 2 }, areaStyle: { color: 'rgba(183,28,28,0.08)' } },
        { name: '概念资金中位数', type: 'line', yAxisIndex: 1, showSymbol: false, data: rows.map((item) => item.concept_median_flow ?? null), lineStyle: { color: '#1565c0', width: 1.5 } },
      ],
    };
  });
  const boardFlowSeriesRows = computed(() => Object.values(boardFlowSeries.value).sort((left, right) => left.label.localeCompare(right.label, 'zh-CN')));
  const boardFlowLatestSnapshot = computed(() => boardFlowSnapshots.value.at(-1) ?? null);
  const boardFlowLatestValues = computed(() => {
    const latest = boardFlowLatestSnapshot.value?.observed_at;
    return boardFlowSeriesRows.value.flatMap((item) => {
      const point = item.points.at(-1);
      return point && point.observed_at === latest ? [{
        key: `${item.taxonomy_key}:${item.sector_key}`, label: item.label, value: point.net_inflow,
      }] : [];
    }).sort((left, right) => right.value - left.value);
  });
  const boardFlowHighlighted = computed(() => new Set([
    ...boardFlowLatestValues.value.slice(0, 10).map((item) => item.key),
    ...boardFlowLatestValues.value.slice(-10).map((item) => item.key),
  ]));
  const boardFlowWindowText = computed(() => boardFlowDisplaySlots.value.length
    ? `${chinaMinute(boardFlowDisplaySlots.value[0])}–${chinaMinute(boardFlowDisplaySlots.value.at(-1))}（上交所）`
    : '等待上交所观察时段');
  const boardFlowGaps = computed(() => {
    const observed = new Set(boardFlowSnapshots.value.map((item) => new Date(item.observed_at).getTime()));
    let gaps = 0; let insideGap = false;
    for (const slot of boardFlowDisplaySlots.value) {
      const missing = !observed.has(new Date(slot).getTime());
      if (missing && !insideGap) gaps += 1;
      insideGap = missing;
    }
    return gaps;
  });
  const boardRotationKind = (item: BoardRotationEvent) => item.event_type === 'cross_zero'
    ? (item.direction === 'inflow' ? '流出转流入' : '流入转流出')
    : (item.direction === 'inflow' ? '流入加速' : '流出加速');
  const boardRotationStateType = (value: BoardRotationEvent['state']): 'success' | 'warning' | 'danger' | 'info' => value === 'alerted' ? 'success' : value === 'confirmed' || value === 'confirming' ? 'warning' : value === 'expired' ? 'info' : 'danger';
  const boardRotationStateText = (value: BoardRotationEvent['state']) => ({ confirming: '待下一分钟确认', confirmed: '已确认', alerted: '已记录', expired: '方向未延续' }[value] ?? value);
  const boardRotationDeliveryText = (_item: BoardRotationEvent) => '仅前端证据';
  const boardFlowChartOption = computed(() => {
    const focus = new Set(boardFlowFocus.value);
    const slots = boardFlowDisplaySlots.value;
    const labels = slots.map((slot) => chinaMinute(slot));
    const lines = boardFlowSeriesRows.value.map((item, index) => {
      const key = `${item.taxonomy_key}:${item.sector_key}`;
      const highlighted = boardFlowHighlighted.value.has(key);
      const latest = item.points.at(-1)?.net_inflow ?? 0;
      const ordered = [...item.points].sort((left, right) => left.observed_at.localeCompare(right.observed_at));
      const realByMinute = new Map(ordered.map((point) => [new Date(point.observed_at).getTime(), point]));
      const firstReal = ordered[0]; let previousReal: BoardFlowPoint | undefined;
      const data = slots.map((slot) => {
        const real = realByMinute.get(new Date(slot).getTime());
        if (real) previousReal = real;
        const source = real ?? previousReal ?? firstReal;
        if (!source) return { value: null, imputed: false, sourceObservedAt: null };
        return {
          value: source.net_inflow, imputed: !real, sourceObservedAt: source.observed_at,
          imputation: real ? null : previousReal ? 'forward_fill' : 'nearest_next',
        };
      });
      const focused = focus.size === 0 || focus.has(key);
      const hue = Math.round((index * 137.508) % 360);
      const color = highlighted ? (latest >= 0 ? '#c62828' : '#16833b') : `hsl(${hue}, 58%, 43%)`;
      return {
        name: item.label, type: 'line', data, showSymbol: false, connectNulls: true,
        animation: false, sampling: 'lttb', emphasis: { focus: 'series', lineStyle: { width: 3, opacity: 1 } },
        lineStyle: { color, width: highlighted ? 2.1 : 0.8, opacity: focused ? (highlighted ? 0.92 : 0.2) : 0.025 },
        itemStyle: { color },
      };
    });
    return {
      animation: false,
      tooltip: {
        trigger: 'item', confine: true,
        formatter: (params: { seriesName?: string; name?: string; data?: { value?: number | null; imputed?: boolean; sourceObservedAt?: string | null } }) => {
          const point = params.data; if (!point || point.value === null || point.value === undefined) return params.seriesName ?? '';
          const fill = point.imputed ? `<br/><span style="color:#b26a00">补点：沿用 ${chinaMinute(point.sourceObservedAt)} 真实值</span>` : '<br/>真实采样';
          return `${params.seriesName ?? ''}<br/>${params.name ?? ''}（上交所）<br/>净流入 ${Number(point.value).toFixed(2)} 亿元${fill}`;
        },
      },
      grid: { left: 62, right: 24, top: 28, bottom: 64 },
      xAxis: { type: 'category', data: labels, boundaryGap: false, name: '上交所时间', axisLabel: { hideOverlap: true }, splitLine: { show: false } },
      yAxis: { type: 'value', name: '净流入（亿元）', axisLine: { show: true, onZero: true }, splitLine: { lineStyle: { color: '#edf0f5' } } },
      dataZoom: [{ type: 'inside', filterMode: 'none' }, { type: 'slider', height: 22, bottom: 14, filterMode: 'none' }],
      series: lines,
    };
  });

  async function loadBoardFlowCurves(reset = false) {
    if (boardFlowLoading.value) return;
    if (reset) { boardFlowSeries.value = {}; boardFlowSnapshots.value = []; boardFlowCursor.value = null; boardFlowFocus.value = []; }
    boardFlowLoading.value = true; boardFlowError.value = '';
    try {
      const params = new URLSearchParams({ taxonomy: boardFlowTaxonomy.value });
      if (boardFlowDate.value) params.set('trade_date', boardFlowDate.value);
      if (!reset && boardFlowCursor.value) params.set('since', boardFlowCursor.value);
      const result = await getJson<BoardFlowResponse>(`/api/research/market/sectors/intraday/curves?${params.toString()}`);
      const merged = { ...boardFlowSeries.value };
      for (const incoming of result.items ?? []) {
        const key = `${incoming.taxonomy_key}:${incoming.sector_key}`;
        const current = merged[key] ?? { ...incoming, points: [] };
        const points = new Map(current.points.map((point) => [point.observed_at, point]));
        for (const point of incoming.points ?? []) points.set(point.observed_at, point);
        merged[key] = { ...incoming, points: [...points.values()].sort((left, right) => left.observed_at.localeCompare(right.observed_at)) };
      }
      const snapshots = new Map(boardFlowSnapshots.value.map((item) => [item.observed_at, item]));
      for (const item of result.snapshots ?? []) snapshots.set(item.observed_at, item);
      boardFlowSeries.value = merged;
      boardFlowSnapshots.value = [...snapshots.values()].sort((left, right) => left.observed_at.localeCompare(right.observed_at));
      boardFlowDate.value = result.trade_date;
      boardFlowDisplaySlots.value = result.display_slots ?? [];
      boardFlowIsExchangeToday.value = Boolean(result.is_exchange_today);
      boardFlowCursor.value = result.cursor ?? boardFlowCursor.value;
      boardFlowNotice.value = result.notice ?? '';
    } catch (error) {
      boardFlowError.value = error instanceof Error ? error.message : String(error);
    } finally { boardFlowLoading.value = false; }
  }
  async function loadMarketFlowFeatures() {
    marketFlowError.value = '';
    try {
      const params = new URLSearchParams();
      if (boardFlowDate.value) params.set('trade_date', boardFlowDate.value);
      const result = await getJson<MarketFlowResponse>(`/api/research/market/flow/features?${params.toString()}`);
      marketFlow.value = result;
      if (!boardFlowDate.value) boardFlowDate.value = result.trade_date;
    } catch (error) {
      marketFlowError.value = error instanceof Error ? error.message : String(error);
    }
  }
  async function loadBoardRotationEvents() {
    try {
      const result = await getJson<{ items?: BoardRotationEvent[] }>('/api/research/intraday/board-rotations/latest?limit=20');
      boardRotationEvents.value = result.items ?? [];
    } catch {
      // Curves remain usable if the optional local rotation-evidence card is unavailable.
    }
  }
  async function loadBoardStockMining() {
    try { boardStockMining.value = await getJson<BoardStockMining>('/api/research/intraday/board-stock-mining/latest?limit=12'); } catch {
      // The rest of the board dashboard remains usable before the migration lands.
    }
  }
  async function loadLimitLinkageMining() {
    try { limitLinkageMining.value = await getJson<LimitLinkageMining>('/api/research/intraday/limit-linkage/latest?limit=20'); } catch {
      // The rest of the board dashboard remains usable before the migration lands.
    }
  }
  function resetBoardFlowCurves() { void loadBoardFlowCurves(true); void loadMarketFlowFeatures(); }

  return {
    boardFlowTaxonomy, boardFlowDate, boardFlowSeries, boardFlowSnapshots, boardFlowCursor, boardFlowLoading, boardFlowError,
    boardFlowNotice, boardFlowFocus, boardFlowDisplaySlots, boardFlowIsExchangeToday,
    boardRotationEvents, boardStockMining, limitLinkageMining, marketFlow, marketFlowError,
    marketFlowLatest, marketFlowSectorHighlights, marketFlowStateLabel, marketFlowStateType,
    sectorFlowTransitionLabel, sectorFlowTransitionType, marketFlowChartOption,
    boardFlowSeriesRows, boardFlowLatestSnapshot, boardFlowLatestValues, boardFlowHighlighted, boardFlowWindowText, boardFlowGaps,
    boardRotationKind, boardRotationStateType, boardRotationStateText, boardRotationDeliveryText, boardFlowChartOption,
    loadBoardFlowCurves, loadMarketFlowFeatures, loadBoardRotationEvents, loadBoardStockMining, loadLimitLinkageMining,
    resetBoardFlowCurves,
  };
}

export type BoardFlowSlice = ReturnType<typeof useBoardFlowSlice>;
