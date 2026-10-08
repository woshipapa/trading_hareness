import { ref } from 'vue';
import type { PaperStatus, StrategyAblation, StrategyFunnel, StrategyGovernance, StrategyHealth } from './types';

/**
 * Read-only strategy runtime evidence: the paper ledger and its funnel, the
 * governance registry, the analyst shadow ablation and strategy health/drift.
 * 数据源 Doctor and 分析师证据 display it; the strategy review context reads
 * the validation gate from `strategyHealth`.
 */
export function useStrategyGovernanceSlice() {
  const paperStatus = ref<PaperStatus>({});
  const strategyFunnel = ref<StrategyFunnel>({});
  const strategyGovernance = ref<StrategyGovernance>({});
  const strategyAblation = ref<StrategyAblation>({});
  const strategyHealth = ref<StrategyHealth>({});

  const paperSectorExposureItems = () => Object.entries(paperStatus.value.latest_portfolio?.payload?.sector_exposure ?? {}).sort((left, right) => right[1] - left[1]).slice(0, 12).map(([sector, value]) => ({ sector, value }));

  return { paperStatus, strategyFunnel, strategyGovernance, strategyAblation, strategyHealth, paperSectorExposureItems };
}

export type StrategyGovernanceSlice = ReturnType<typeof useStrategyGovernanceSlice>;
