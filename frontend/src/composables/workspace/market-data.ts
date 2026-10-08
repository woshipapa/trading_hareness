import { ref } from 'vue';
import { ElMessage } from 'element-plus';
import type { Announcement, ConceptCandidate, ConceptSignal, MarketSnapshot, ProviderApiCapability, Sector, SectorFlow } from './types';
import type { ResearchShell } from './shell';

/**
 * 全市场快照: midday/close market snapshots, the Tonghuashun sector directory
 * and member batches, industry and concept flows, limit-up concept candidates,
 * official announcements and the provider capability facts.
 *
 * `sectorFlowDate` is the trading date the research sync actions run for; the
 * close-review actions read the same ref.
 */
export function useMarketDataSlice(shell: ResearchShell) {
  const { runAction } = shell;

  const providerApiCapabilities = ref<ProviderApiCapability[]>([]);
  const marketSnapshots = ref<MarketSnapshot[]>([]);
  const sectors = ref<Sector[]>([]);
  const sectorFlows = ref<SectorFlow[]>([]);
  const conceptSignals = ref<ConceptSignal[]>([]);
  const conceptCandidates = ref<ConceptCandidate[]>([]);
  const announcements = ref<Announcement[]>([]);
  const sectorMemberOffset = ref(0); const sectorMemberLimit = ref(10); const sectorFlowDate = ref('');

  const snapshotType = (value?: string) => value === 'ready' ? 'success' : value === 'degraded' ? 'warning' : value === 'blocked' || value === 'failed' ? 'danger' : 'info';

  async function syncAllMarketUniverse() { await runAction('刷新全市场股票池', '/api/research/market/universe/sync', {}, true); }
  async function runMarketSnapshot(session: 'midday' | 'close') { await runAction(session === 'midday' ? '生成午盘全市场快照' : '生成收盘全市场快照', '/api/research/market/snapshots/run', { session, universe_key: 'all_a', refresh_public_quotes: true }, true); }
  async function syncFullMarketDaily() { await runAction('同步全市场收盘日线', '/api/research/market/full-daily/sync', {}, true); }
  async function syncSectorDirectory() { await runAction('同步同花顺板块目录', '/api/research/market/sectors/sync', { all_types: true, sync_members: false }, true); }
  async function syncSectorMembers() { await runAction('同步板块成分批次', '/api/research/market/sectors/sync', { index_type: 'N', sync_members: true, member_offset: sectorMemberOffset.value, member_limit: sectorMemberLimit.value }, true); }
  async function syncSectorFlows() { if (!sectorFlowDate.value) { ElMessage.error('请选择交易日'); return; } await runAction('同步同花顺行业资金流', '/api/research/market/sector-flows/sync', { trade_date: sectorFlowDate.value, provider: 'super' }, true); }
  async function syncConceptSignals() { if (!sectorFlowDate.value) { ElMessage.error('请选择交易日'); return; } await runAction('同步概念资金流与涨停强度', '/api/research/market/sectors/concepts/sync', { trade_date: sectorFlowDate.value, provider: 'super' }, true); }
  async function syncConceptCandidates() { if (!sectorFlowDate.value) { ElMessage.error('请选择交易日'); return; } await runAction('生成概念涨停候选', '/api/research/market/sectors/concepts/candidates/sync', { trade_date: sectorFlowDate.value, provider: 'super', top_concepts: 8, leaders_per_concept: 3 }, true); }
  async function runBoardResearch() { if (!sectorFlowDate.value) { ElMessage.error('请选择交易日'); return; } await runAction('板块到个股一键研究', '/api/research/market/sectors/concepts/research/run', { trade_date: sectorFlowDate.value, provider: 'super', top_concepts: 8, leaders_per_concept: 3, max_stock_studies: 6, study_lookback_days: 21, sync_announcements: true }, true); }
  async function syncCninfoAnnouncements() { const symbols = conceptCandidates.value.slice(0, 20).map((item) => item.symbol); await runAction('同步巨潮公告', '/api/research/events/cninfo/sync', { symbols, universe_key: 'core', lookback_days: 45, max_pages_per_symbol: 1 }, true); }

  return {
    providerApiCapabilities, marketSnapshots, sectors, sectorFlows, conceptSignals, conceptCandidates, announcements,
    sectorMemberOffset, sectorMemberLimit, sectorFlowDate,
    snapshotType,
    syncAllMarketUniverse, runMarketSnapshot, syncFullMarketDaily, syncSectorDirectory, syncSectorMembers,
    syncSectorFlows, syncConceptSignals, syncConceptCandidates, runBoardResearch, syncCninfoAnnouncements,
  };
}

export type MarketDataSlice = ReturnType<typeof useMarketDataSlice>;
