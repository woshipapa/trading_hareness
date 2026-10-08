import { computed, ref } from 'vue';
import { ElMessage } from 'element-plus';
import {
  annotationStorageKey,
  buildManualAnnotationSeries,
  buildTonghuashunDeepLink,
  buildTonghuashunWebLink,
  type AnalystChartAnnotation,
} from '../../research/analyst-chart';
import type { AnalystStockTimeline } from './types';
import { chinaDateTime, chinaMinute } from './format';
import type { ResearchShell } from './shell';
import type { OutcomesSlice } from './outcomes';
import type { StockStudySlice } from './stock-study';

/**
 * 分析师个股动作 × 分钟K线: one symbol's minute bars with analyst actions and
 * strategy signal entries, local-only chart annotations (kept in this
 * browser's localStorage) and the Tonghuashun hand-off.  The symbol is the
 * stock study's `studySymbol`.
 */
export function useAnalystTimelineSlice(
  shell: ResearchShell,
  stockStudy: Pick<StockStudySlice, 'studySymbol'>,
  outcomes: Pick<OutcomesSlice, 'intradayOutcomes'>,
) {
  const { getJson } = shell.transport;
  const { studySymbol } = stockStudy;
  const { intradayOutcomes } = outcomes;

  const analystStockTimeline = ref<AnalystStockTimeline | null>(null);
  const analystStockTimelineLoading = ref(false);
  const analystStockTimelineError = ref('');
  const analystTimelineAnalyst = ref('');
  const analystTimelineDate = ref('');
  const analystChartAnnotations = ref<AnalystChartAnnotation[]>([]);
  const analystAnnotationSelectionMode = ref<'point' | 'area-start' | 'area-end'>('point');
  const analystAnnotationPointIndex = ref<number | null>(null);
  const analystAnnotationAreaStartIndex = ref<number | null>(null);
  const analystAnnotationAreaEndIndex = ref<number | null>(null);
  const analystAnnotationPrice = ref<number | null>(null);
  const analystAnnotationLower = ref<number | null>(null);
  const analystAnnotationUpper = ref<number | null>(null);
  const analystAnnotationLabel = ref('');

  const analystStockDeepLink = computed(() => buildTonghuashunDeepLink(studySymbol.value));
  const analystAnnotationSelectionLabel = computed(() => {
    const bars = analystStockTimeline.value?.bars ?? [];
    const format = (index: number | null) => index === null || !bars[index] ? '-' : chinaMinute(bars[index].bar_time);
    return `点 ${format(analystAnnotationPointIndex.value)} · 区间 ${format(analystAnnotationAreaStartIndex.value)} → ${format(analystAnnotationAreaEndIndex.value)}`;
  });
  const analystStockTimelineChartOption = computed(() => {
    const timeline = analystStockTimeline.value;
    const bars = timeline?.bars ?? [];
    const indexByTime = new Map(bars.map((bar, index) => [bar.bar_time, index]));
    const manual = buildManualAnnotationSeries(analystChartAnnotations.value, bars.length);
    const markerData = (timeline?.actions ?? []).filter((action) => action.mapping_status === 'mapped' && action.nearest_bar_time && indexByTime.has(action.nearest_bar_time)).map((action) => {
      const index = indexByTime.get(action.nearest_bar_time as string) ?? 0;
      const color = ['buy', 'watch', 'add_t', 'hold'].includes(action.action) ? '#d32f2f' : ['sell', 'reduce', 'avoid'].includes(action.action) ? '#1565c0' : '#f9a825';
      return { value: [index, action.nearest_bar_close ?? 0], name: `${action.analyst_id} · ${action.action}`, action, itemStyle: { color }, label: { show: true, formatter: action.action, color, fontSize: 10, position: 'top' } };
    });
    const strategyMarkerData = (timeline ? intradayOutcomes.value.filter((outcome) => outcome.symbol === timeline.symbol) : []).flatMap((outcome) => {
      if (!Number.isFinite(outcome.entry_price)) return [];
      const entryTime = new Date(outcome.entry_observed_at).getTime();
      if (!Number.isFinite(entryTime) || !bars.length) return [];
      const index = bars.reduce((closest, bar, candidate) => Math.abs(new Date(bar.bar_time).getTime() - entryTime) < Math.abs(new Date(bars[closest].bar_time).getTime() - entryTime) ? candidate : closest, 0);
      const offsetSeconds = Math.abs(new Date(bars[index].bar_time).getTime() - entryTime) / 1000;
      if (offsetSeconds > 20 * 60) return [];
      const color = outcome.direction > 0 ? '#c62828' : outcome.direction < 0 ? '#1565c0' : '#f9a825';
      return [{ value: [index, outcome.entry_price], name: `策略 · ${outcome.signal_type}`, outcome, itemStyle: { color }, label: { show: true, formatter: outcome.signal_type, color, fontSize: 10, position: 'bottom' } }];
    });
    return { animation: false, tooltip: { trigger: 'axis' }, grid: { left: 52, right: 18, top: 34, bottom: 46 }, xAxis: { type: 'category', data: bars.map((bar) => chinaMinute(bar.bar_time)), boundaryGap: true }, yAxis: { type: 'value', scale: true }, dataZoom: [{ type: 'inside', filterMode: 'none' }], series: [
      { name: '分钟K线', type: 'candlestick', data: bars.map((bar) => [bar.open, bar.close, bar.low, bar.high]), itemStyle: { color: '#d32f2f', color0: '#1565c0', borderColor: '#d32f2f', borderColor0: '#1565c0' }, markPoint: manual.points.length ? { symbol: 'pin', symbolSize: 42, data: manual.points } : undefined, markLine: manual.lines.length ? { silent: true, symbol: ['none', 'none'], data: manual.lines, lineStyle: { color: '#7b1fa2', type: 'dashed' }, label: { color: '#7b1fa2' } } : undefined, markArea: manual.areas.length ? { silent: true, data: manual.areas, itemStyle: { color: 'rgba(123,31,162,0.14)' }, label: { color: '#7b1fa2' } } : undefined },
      { name: '策略盘中信号', type: 'scatter', data: strategyMarkerData, symbol: 'diamond', symbolSize: 14, z: 11, tooltip: { formatter: (params: { data?: { outcome?: { signal_type?: string; status?: string; raw_return?: number | null; tradability?: string } } }) => { const outcome = params.data?.outcome; return `策略 · ${outcome?.signal_type ?? ''}<br/>状态：${outcome?.status ?? '-'}<br/>方向收益：${outcome?.raw_return === null || outcome?.raw_return === undefined ? '-' : `${(outcome.raw_return * 100).toFixed(3)}%`}<br/>测量：${outcome?.tradability ?? '-'}`; } } },
      { name: '分析师动作', type: 'scatter', data: markerData, symbolSize: 12, z: 10, tooltip: { formatter: (params: { data?: { action?: { analyst_id?: string; action?: string; event_time?: string; evidence?: string } } }) => { const action = params.data?.action; return `${action?.analyst_id ?? ''} · ${action?.action ?? ''}<br/>${action?.event_time ? chinaDateTime(action.event_time) : ''}<br/>${action?.evidence ?? ''}`; } } },
    ] };
  });
  function analystAnnotationKey() {
    const timeline = analystStockTimeline.value;
    return timeline ? annotationStorageKey(timeline.symbol, timeline.start_date, timeline.end_date) : '';
  }
  function persistAnalystAnnotations() {
    const key = analystAnnotationKey();
    if (key) localStorage.setItem(key, JSON.stringify(analystChartAnnotations.value));
  }
  function loadAnalystAnnotations(timeline: AnalystStockTimeline) {
    analystChartAnnotations.value = [];
    analystAnnotationPointIndex.value = null;
    analystAnnotationAreaStartIndex.value = null;
    analystAnnotationAreaEndIndex.value = null;
    analystAnnotationPrice.value = null;
    analystAnnotationLower.value = null;
    analystAnnotationUpper.value = null;
    try {
      const raw = localStorage.getItem(annotationStorageKey(timeline.symbol, timeline.start_date, timeline.end_date));
      const parsed = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(parsed)) return;
      analystChartAnnotations.value = parsed.filter((item): item is AnalystChartAnnotation => (
        item && typeof item === 'object'
        && typeof item.id === 'string'
        && ['point', 'line', 'area'].includes(item.kind)
        && Number.isInteger(item.start_index)
      ));
    } catch {
      analystChartAnnotations.value = [];
    }
  }
  function handleAnalystChartClick(params: { seriesType?: string; dataIndex?: number }) {
    if (params.seriesType !== 'candlestick' || !Number.isInteger(params.dataIndex)) return;
    const index = Number(params.dataIndex);
    const bar = analystStockTimeline.value?.bars[index];
    if (!bar) return;
    if (analystAnnotationSelectionMode.value === 'area-start') analystAnnotationAreaStartIndex.value = index;
    else if (analystAnnotationSelectionMode.value === 'area-end') analystAnnotationAreaEndIndex.value = index;
    else {
      analystAnnotationPointIndex.value = index;
      if (analystAnnotationPrice.value === null) analystAnnotationPrice.value = bar.close;
    }
  }
  function addAnalystChartAnnotation(kind: AnalystChartAnnotation['kind']) {
    const timeline = analystStockTimeline.value;
    if (!timeline) return;
    const label = analystAnnotationLabel.value.trim() || (kind === 'point' ? '自定义点' : kind === 'line' ? '自定义价位' : '自定义区域');
    const base = { id: `manual-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, kind, label };
    if (kind === 'point' || kind === 'line') {
      const index = analystAnnotationPointIndex.value;
      if (index === null || !Number.isFinite(analystAnnotationPrice.value)) {
        ElMessage.warning('先点击一根K线选择位置，并填写价格');
        return;
      }
      analystChartAnnotations.value = [...analystChartAnnotations.value, { ...base, start_index: index, price: analystAnnotationPrice.value as number }];
    } else {
      const start = analystAnnotationAreaStartIndex.value;
      const end = analystAnnotationAreaEndIndex.value;
      if (start === null || end === null || !Number.isFinite(analystAnnotationLower.value) || !Number.isFinite(analystAnnotationUpper.value)) {
        ElMessage.warning('先分别选择区域起点、终点，并填写上下边界');
        return;
      }
      const lower = analystAnnotationLower.value as number;
      const upper = analystAnnotationUpper.value as number;
      if (lower > upper) {
        ElMessage.warning('区域下边界不能高于上边界');
        return;
      }
      analystChartAnnotations.value = [...analystChartAnnotations.value, { ...base, start_index: Math.min(start, end), end_index: Math.max(start, end), lower, upper }];
    }
    persistAnalystAnnotations();
    analystAnnotationLabel.value = '';
    ElMessage.success('自定义标记已加入当前研究图（仅保存在本机浏览器）');
  }
  function clearAnalystChartAnnotations() {
    analystChartAnnotations.value = [];
    persistAnalystAnnotations();
  }
  function openAnalystStockInTonghuashun(symbol = studySymbol.value) {
    const appLink = buildTonghuashunDeepLink(symbol);
    const webLink = buildTonghuashunWebLink(symbol);
    if (!appLink || !webLink) {
      ElMessage.warning('请输入带交易所后缀的代码，例如 600000.SH');
      return;
    }
    // Always open a normal HTTPS chart in a new tab. The custom protocol is
    // attempted separately so a missing handler cannot replace this dashboard.
    window.open(webLink, '_blank', 'noopener,noreferrer');
    const launcher = document.createElement('a');
    launcher.href = appLink;
    launcher.target = '_blank';
    launcher.rel = 'noopener noreferrer';
    launcher.style.display = 'none';
    document.body.appendChild(launcher);
    launcher.click();
    launcher.remove();
  }
  async function loadAnalystStockTimeline() {
    const symbol = studySymbol.value.trim().toUpperCase();
    if (!/^\d{6}\.(SH|SZ|BJ)$/.test(symbol)) { analystStockTimelineError.value = '请输入形如 603459.SH 的代码'; return; }
    analystStockTimelineLoading.value = true; analystStockTimelineError.value = '';
    try {
      const params = new URLSearchParams({ symbol, limit: '1500' });
      if (analystTimelineDate.value) { params.set('start_date', analystTimelineDate.value); params.set('end_date', analystTimelineDate.value); }
      if (analystTimelineAnalyst.value) params.set('analyst_id', analystTimelineAnalyst.value);
      analystStockTimeline.value = await getJson<AnalystStockTimeline>(`/api/research/analyst-research/stock-timeline?${params.toString()}`);
      loadAnalystAnnotations(analystStockTimeline.value);
    } catch (error) { analystStockTimeline.value = null; analystStockTimelineError.value = error instanceof Error ? error.message : String(error); } finally { analystStockTimelineLoading.value = false; }
  }

  return {
    analystStockTimeline, analystStockTimelineLoading, analystStockTimelineError, analystTimelineAnalyst, analystTimelineDate,
    analystStockDeepLink, analystChartAnnotations, analystAnnotationSelectionMode, analystAnnotationSelectionLabel,
    analystAnnotationPointIndex, analystAnnotationAreaStartIndex, analystAnnotationAreaEndIndex,
    analystAnnotationPrice, analystAnnotationLower, analystAnnotationUpper, analystAnnotationLabel,
    analystStockTimelineChartOption, handleAnalystChartClick, addAnalystChartAnnotation, clearAnalystChartAnnotations,
    openAnalystStockInTonghuashun, loadAnalystStockTimeline,
  };
}

export type AnalystTimelineSlice = ReturnType<typeof useAnalystTimelineSlice>;
