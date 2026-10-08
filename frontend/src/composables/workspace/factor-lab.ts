import { computed, ref } from 'vue';
import type { Factor, FactorEvaluation, Framework, Strategy, StrategyExperiment, TrainingRoadmap } from './types';
import { nestedValue } from './format';
import type { ResearchShell } from './shell';

/**
 * 因子与回测: the factor registry and evaluations, A-share constrained
 * backtests, the watchlist main-wave and rebound shadow models, and the
 * framework/training roadmap.
 */
export function useFactorLabSlice(shell: ResearchShell) {
  const { runAction } = shell;

  const factors = ref<Factor[]>([]);
  const factorEvaluations = ref<FactorEvaluation[]>([]);
  const strategies = ref<Strategy[]>([]);
  const strategyExperiments = ref<StrategyExperiment[]>([]);
  const mainWaveExperiments = ref<StrategyExperiment[]>([]);
  const frameworks = ref<Framework[]>([]);
  const trainingRoadmap = ref<TrainingRoadmap>({ status: 'planned', policy: '', stages: [] });
  const selectedFactors = ref<string[]>([]);
  const factorHorizon = ref(5);
  const backtestForm = ref({ rebalance_days: 5, hold_days: 5, top_n: 20, total_cost_bps: 18 });

  const latestFactorEvaluations = computed(() => {
    const seen = new Set<string>();
    return factorEvaluations.value.filter((item) => {
      if (seen.has(item.factor_key)) return false;
      seen.add(item.factor_key); return true;
    });
  });
  const factorChartOption = computed(() => ({
    tooltip: { trigger: 'axis' }, legend: { data: ['全样本中性IC', '样本外IC'] }, grid: { left: 48, right: 18, top: 42, bottom: 62 }, xAxis: { type: 'category', data: latestFactorEvaluations.value.map((item) => item.label || item.factor_key), axisLabel: { rotate: 35 } }, yAxis: { type: 'value', name: 'Rank IC' }, series: [
      { name: '全样本中性IC', type: 'bar', data: latestFactorEvaluations.value.map((item) => Number(nestedValue(item.metrics, 'neutral_rank_ic.mean')) || 0), itemStyle: { color: '#1976d2' } },
      { name: '样本外IC', type: 'bar', data: latestFactorEvaluations.value.map((item) => Number(nestedValue(item.metrics, 'walk_forward.test.neutral_rank_ic.mean')) || 0), itemStyle: { color: '#ef6c00' } },
    ],
  }));
  const latestExperiment = computed(() => strategyExperiments.value[0] ?? null);
  const latestMainWaveExperiment = computed(() => mainWaveExperiments.value.find((item) => item.strategy_key === 'watchlist_main_wave_shadow_v2') ?? null);
  const latestReboundExperiment = computed(() => mainWaveExperiments.value.find((item) => item.strategy_key === 'watchlist_countertrend_rebound_shadow_v1') ?? null);
  const mainWaveCurrentScores = computed<Record<string, unknown>[]>(() => {
    const value = nestedValue(latestMainWaveExperiment.value?.metrics, 'current_scores');
    return Array.isArray(value) ? value as Record<string, unknown>[] : [];
  });
  const mainWaveQualification = computed(() => {
    const value = nestedValue(latestMainWaveExperiment.value?.metrics, 'pattern_summary.qualification');
    return value && typeof value === 'object' ? Object.entries(value as Record<string, unknown>).map(([key, threshold]) => ({ key, threshold })) : [];
  });
  const mainWaveFailedChecks = computed(() => {
    const value = nestedValue(latestMainWaveExperiment.value?.metrics, 'promotion_gate.checks');
    return value && typeof value === 'object' ? Object.entries(value as Record<string, unknown>).filter(([, passed]) => passed !== true).map(([key]) => key) : [];
  });
  const reboundCurrentScores = computed<Record<string, unknown>[]>(() => {
    const value = nestedValue(latestReboundExperiment.value?.metrics, 'current_scores');
    return Array.isArray(value) ? value as Record<string, unknown>[] : [];
  });
  const reboundFailedChecks = computed(() => {
    const value = nestedValue(latestReboundExperiment.value?.metrics, 'promotion_gate.checks');
    return value && typeof value === 'object' ? Object.entries(value as Record<string, unknown>).filter(([, passed]) => passed !== true).map(([key]) => key) : [];
  });
  const equityChartOption = computed(() => ({
    tooltip: { trigger: 'axis' }, grid: { left: 48, right: 18, top: 24, bottom: 40 }, xAxis: { type: 'category', data: latestExperiment.value?.equity_curve.map((item) => item.date) ?? [] }, yAxis: { type: 'value', name: '净值', scale: true }, series: [{ type: 'line', smooth: true, showSymbol: false, data: latestExperiment.value?.equity_curve.map((item) => item.equity) ?? [], lineStyle: { width: 2, color: '#00897b' }, areaStyle: { color: 'rgba(0,137,123,0.12)' } }],
  }));

  async function runFactorEvaluation() { await runAction('评估因子', '/api/research/factors/evaluate', { universe_key: 'all_a', factor_keys: selectedFactors.value, horizon_days: factorHorizon.value }); }
  async function runStrategyBacktest() { await runAction('运行A股约束回测', '/api/research/strategies/backtest', { strategy_key: 'multi_factor_rank_v1', universe_key: 'all_a', factors: selectedFactors.value, ...backtestForm.value }); }
  async function runMainWaveResearch() { await runAction('训练观察池主升影子模型', '/api/research/strategy/watchlist-main-wave/run', {}, true); }

  return {
    factors, factorEvaluations, strategies, strategyExperiments, mainWaveExperiments, frameworks, trainingRoadmap,
    selectedFactors, factorHorizon, backtestForm,
    latestFactorEvaluations, factorChartOption, latestExperiment, latestMainWaveExperiment, latestReboundExperiment,
    mainWaveCurrentScores, mainWaveQualification, mainWaveFailedChecks, reboundCurrentScores, reboundFailedChecks, equityChartOption,
    runFactorEvaluation, runStrategyBacktest, runMainWaveResearch,
  };
}

export type FactorLabSlice = ReturnType<typeof useFactorLabSlice>;
