<script setup lang="ts">
import { computed, inject, ref } from 'vue';
import { Refresh, Link, Search } from '@element-plus/icons-vue';
import { dashboardContextKey } from '../../dashboard-context';
import { useRemoteQuantRuntime } from '../../composables/useRemoteQuantRuntime';
import { executorCapacityRows, freshnessOf, freshnessLabel, scanOverlapRows as buildScanOverlapRows, scanStageRows as buildScanStageRows, symbolFromRow } from '../../research/remote-runtime';

const dashboard = inject(dashboardContextKey);
if (!dashboard) throw new Error('research tab requires the dashboard shell context');
const runtime = useRemoteQuantRuntime();
const selectedCardOpen = ref(false);
const mobileLayout = (dashboard as Record<string, any>).mobileLayout;
const dateText = (value: unknown) => value ? new Date(String(value)).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '-';
const symbolText = (row: Record<string, any>) => symbolFromRow(row) ?? '-';
const openTonghuashun = (row: Record<string, any>) => {
  const symbol = symbolFromRow(row);
  if (symbol) (dashboard as Record<string, any>).openAnalystStockInTonghuashun(symbol);
};
const inspectCard = async (row: Record<string, any>) => {
  const symbol = symbolFromRow(row);
  if (!symbol) return;
  selectedCardOpen.value = true;
  await runtime.loadDecisionCard(symbol);
};
const openCardTonghuashun = () => {
  const symbol = runtime.decisionCard.value?.symbol;
  if (symbol) (dashboard as Record<string, any>).openAnalystStockInTonghuashun(symbol);
};
const scanFreshness = computed(() => freshnessOf(runtime.latestScan.value?.scan?.observed_at));
const scanPerformance = computed(() => runtime.latestScan.value?.scan?.source_status?.scan_performance ?? runtime.latestScan.value?.scan_performance ?? {});
const scanStageRows = computed(() => buildScanStageRows(scanPerformance.value.stage_ms, scanPerformance.value.total_ms));
const scanOverlapRows = computed(() => buildScanOverlapRows(scanPerformance.value.overlap));
const executorRows = computed(() => executorCapacityRows(runtime.runtimeHealth.value.blocking_executors));
const runtimeResources = computed(() => runtime.runtimeHealth.value.resources ?? {});
const memoryResource = computed(() => runtimeResources.value.memory ?? {});
const diskResource = computed(() => runtimeResources.value.disk ?? {});
const resourcePercent = (value: unknown) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric >= 0 ? Math.min(100, Math.round(numeric * 1000) / 10) : null;
};
const memoryUsagePct = computed(() => resourcePercent(memoryResource.value.ratio));
const diskUsagePct = computed(() => {
  const free = resourcePercent(diskResource.value.free_ratio);
  return free === null ? null : Math.max(0, Math.round((100 - free) * 10) / 10);
});
const formatBytes = (value: unknown) => {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return '-';
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MiB`;
  return `${(bytes / 1024).toFixed(0)} KiB`;
};
const scanStageLabel = (stage: string) => ({
  session_and_watchlist: '会话与观察池',
  pre_capture: '采集前准备',
  market_wide_research: '全市场研究',
  capture_and_memberships: '行情与成员关系',
  enrichment_and_strategy_prepare: '分钟增强与策略准备',
  signal_persist: '信号事务落库',
  delivery_and_research: '提醒与研究分支',
  shadow_rotation: '轮换影子研究',
}[stage] ?? stage);
const scanOverlapLabel = (key: string) => ({
  capture_and_memberships: '行情与成员关系',
  capture_and_board_cache: '行情与板流状态',
  enrichment_and_board_cache: '分钟增强与板流状态',
  leader_flow_and_primary_persist: '全市场研究与主事务',
  alert_delivery_and_leader_flow: '提醒投递与研究分支',
  shadow_rotation_and_delivery: '影子轮换与投递',
}[key] ?? key);
const scanOverlapValue = (value: string) => ({
  concurrent: '并行',
  concurrent_after_watchlist: '观察池后并行',
  concurrent_after_capture: '采集后并行',
  concurrent_after_receipt: '回执后并行',
}[value] ?? value);
const healthState = (value: unknown) => value === 'healthy' || value === 'ready' ? 'success' : ['degraded', 'unavailable', 'disabled'].includes(String(value)) ? 'danger' : 'warning';
const executorStateType = (value: string) => value === 'saturated' ? 'danger' : value === 'busy' ? 'warning' : 'success';
const executorLabel = (value: string) => ({
  public_source: '公共源读取',
  realtime_vendor: '实时行情源',
  database: '数据库阻塞读写',
  tdx_shadow: 'TDX 影子写入',
  auction_capture: '竞价采集',
  super_get: 'Super GET',
}[value] ?? value);
const liveEffect = computed(() => runtime.strategyHealth.value.validation_gate?.live_effect ?? runtime.promotion.value.live_effect ?? 'none');
</script>

<template>
  <el-alert title="远端 47 owner 只读观察台：实时数据、盘中扫描和策略/复盘结果来自已落库证据。所有结果保持研究模式，不会自动下单。" type="warning" show-icon :closable="false" class="section-gap" />
  <el-card shadow="never" class="section-gap">
    <template #header><div class="card-header"><span>Owner 运行状态 <small class="block-note">水位/扫描 15 秒；盘后研究 60 秒</small></span><el-button text :icon="Refresh" :loading="runtime.loading.value" @click="runtime.loadRemoteRuntimeCore">刷新</el-button></div></template>
    <el-alert v-if="runtime.error.value" :title="runtime.error.value" type="warning" :closable="false" show-icon class="section-gap" />
    <el-descriptions :column="mobileLayout ? 1 : 5" border size="small">
      <el-descriptions-item label="最近读取">{{ runtime.lastAttemptAt.value ? dateText(runtime.lastAttemptAt.value) : '尚未发起读取' }}<small v-if="runtime.lastSuccessAt.value" class="block-note">成功 {{ dateText(runtime.lastSuccessAt.value) }}</small></el-descriptions-item>
      <el-descriptions-item label="远端网络">{{ runtime.runtimeHealth.value.network?.state ?? '-' }}</el-descriptions-item>
      <el-descriptions-item label="运行 SHA">{{ runtime.runtimeHealth.value.build?.running_sha ?? runtime.runtimeHealth.value.build?.git_sha ?? '-' }}</el-descriptions-item>
      <el-descriptions-item label="发布">{{ runtime.runtimeHealth.value.build?.release ?? '-' }}</el-descriptions-item>
      <el-descriptions-item label="策略 live_effect"><el-tag type="warning">{{ liveEffect }}</el-tag></el-descriptions-item>
    </el-descriptions>
    <el-space wrap class="section-gap"><el-tag v-for="item in runtime.services.value.items ?? []" :key="item.key" :type="healthState(item.state)">{{ item.label ?? item.key }}：{{ item.state ?? '-' }}</el-tag></el-space>
  </el-card>

  <el-card shadow="never" class="section-gap" header="本地并发水位（研究观测）">
    <el-alert title="仅展示当前进程的有界 executor 使用情况；达到饱和时应观察排队和拒绝率，不会自动突破 provider、数据库或硬件上限。" type="info" :closable="false" show-icon />
    <div v-if="executorRows.length" class="executor-capacity-grid">
      <div v-for="row in executorRows" :key="row.key" class="executor-capacity-item">
        <div class="executor-capacity-heading"><strong>{{ executorLabel(row.key) }}</strong><el-tag size="small" :type="executorStateType(row.state)">{{ row.state === 'saturated' ? '已饱和' : row.state === 'busy' ? '繁忙' : '可用' }}</el-tag></div>
        <el-progress :percentage="row.utilizationPct" :status="executorStateType(row.state) === 'success' ? 'success' : executorStateType(row.state) === 'warning' ? 'warning' : 'exception'" :stroke-width="8" :show-text="false" />
        <small>占用 {{ row.occupied }}/{{ row.workers + row.queueCapacity }} · worker {{ row.workers }} · 排队 {{ row.queueCapacity }} · 可用槽位 {{ row.availableSlots }}</small>
      </div>
    </div>
    <el-empty v-else description="当前发布未提供 executor 水位" :image-size="38" />
  </el-card>

  <el-card shadow="never" class="section-gap" header="资源与网络水位">
    <el-alert title="资源读数来自 owner health；它们只用于容量判断和故障定位，不会改变研究策略或信号资格。" type="info" :closable="false" show-icon />
    <el-row :gutter="12" class="resource-capacity-grid">
      <el-col :xs="24" :sm="8"><div class="resource-capacity-item"><div class="executor-capacity-heading"><strong>进程内存</strong><el-tag size="small" :type="memoryUsagePct !== null && memoryUsagePct >= 85 ? 'danger' : memoryUsagePct !== null && memoryUsagePct >= 70 ? 'warning' : 'success'">{{ memoryUsagePct === null ? '-' : `${memoryUsagePct}%` }}</el-tag></div><el-progress v-if="memoryUsagePct !== null" :percentage="memoryUsagePct" :status="memoryUsagePct >= 85 ? 'exception' : memoryUsagePct >= 70 ? 'warning' : 'success'" :stroke-width="8" :show-text="false" /><small>{{ formatBytes(memoryResource.rss_bytes) }} / {{ formatBytes(memoryResource.cgroup_limit_bytes) }}</small></div></el-col>
      <el-col :xs="24" :sm="8"><div class="resource-capacity-item"><div class="executor-capacity-heading"><strong>磁盘使用</strong><el-tag size="small" :type="diskUsagePct !== null && diskUsagePct >= 90 ? 'danger' : diskUsagePct !== null && diskUsagePct >= 75 ? 'warning' : 'success'">{{ diskUsagePct === null ? '-' : `${diskUsagePct}%` }}</el-tag></div><el-progress v-if="diskUsagePct !== null" :percentage="diskUsagePct" :status="diskUsagePct >= 90 ? 'exception' : diskUsagePct >= 75 ? 'warning' : 'success'" :stroke-width="8" :show-text="false" /><small>剩余 {{ formatBytes(diskResource.free_bytes) }} · {{ diskResource.free_ratio == null ? '-' : `${resourcePercent(diskResource.free_ratio)}%` }} 可用</small></div></el-col>
      <el-col :xs="24" :sm="8"><div class="resource-capacity-item"><div class="executor-capacity-heading"><strong>网络观测</strong><el-tag size="small" :type="healthState(runtime.runtimeHealth.value.network?.state)">{{ runtime.runtimeHealth.value.network?.state ?? '-' }}</el-tag></div><small>最近成功：{{ dateText(runtime.runtimeHealth.value.network?.last_success_at) }}<br/>最近来源：{{ runtime.runtimeHealth.value.network?.last_source ?? '-' }}</small></div></el-col>
    </el-row>
  </el-card>

  <el-row :gutter="14" class="section-gap">
    <el-col :xs="12" :md="6"><el-card shadow="never" class="metric-card"><span>观察池</span><strong>{{ runtime.watchlistItems.value.length }}</strong></el-card></el-col>
    <el-col :xs="12" :md="6"><el-card shadow="never" class="metric-card"><span>最新信号</span><strong>{{ runtime.signals.value.length }}</strong></el-card></el-col>
    <el-col :xs="12" :md="6"><el-card shadow="never" class="metric-card"><span>扫描时间</span><strong class="metric-text">{{ dateText(runtime.latestScan.value.scan?.observed_at) }}</strong></el-card></el-col>
    <el-col :xs="12" :md="6"><el-card shadow="never" class="metric-card"><span>扫描新鲜度</span><strong><el-tag :type="scanFreshness === 'fresh' ? 'success' : 'warning'">{{ freshnessLabel(scanFreshness) }}</el-tag></strong></el-card></el-col>
  </el-row>

  <el-card shadow="never" class="section-gap" header="实时扫描性能（研究遥测）">
    <el-alert title="阶段耗时只用于判断并行收益和数据库瓶颈，不参与信号、阈值或任何执行路径。" type="info" :closable="false" show-icon />
    <el-descriptions :column="mobileLayout ? 1 : 4" border size="small" class="section-gap">
      <el-descriptions-item label="总耗时">{{ scanPerformance.total_ms != null ? `${scanPerformance.total_ms} ms` : '-' }}</el-descriptions-item>
      <el-descriptions-item label="遥测状态"><el-tag :type="scanPerformance.persistence_status === 'persisted' ? 'success' : 'warning'">{{ scanPerformance.persistence_status ?? '暂无' }}</el-tag></el-descriptions-item>
      <el-descriptions-item label="并行边界">{{ Object.keys(scanPerformance.overlap ?? {}).length || 0 }} 处</el-descriptions-item>
      <el-descriptions-item label="版本">{{ scanPerformance.schema_version ?? '-' }}</el-descriptions-item>
    </el-descriptions>
    <div v-if="scanStageRows.length" class="scan-performance-visuals">
      <div class="scan-stage-visual">
        <div class="scan-visual-heading"><strong>阶段耗时比例</strong><small>相对本次扫描总耗时</small></div>
        <div v-for="row in scanStageRows" :key="row.stage" class="scan-stage-visual-row">
          <div class="scan-stage-visual-label"><span>{{ scanStageLabel(row.stage) }}</span><strong>{{ row.milliseconds }} ms</strong></div>
          <div class="scan-stage-track" role="img" :aria-label="`${scanStageLabel(row.stage)} ${row.milliseconds} ms，占总耗时 ${row.share}%`"><span class="scan-stage-fill" :style="{ width: `${row.share}%` }" /></div>
        </div>
      </div>
      <div class="scan-overlap-visual">
        <div class="scan-visual-heading"><strong>Overlap 编排</strong><el-tag size="small" type="success">{{ scanOverlapRows.length }} 处</el-tag></div>
        <el-space v-if="scanOverlapRows.length" wrap>
          <el-tag v-for="row in scanOverlapRows" :key="row.key" size="small" type="info">{{ scanOverlapLabel(row.key) }} · {{ scanOverlapValue(row.value) }}</el-tag>
        </el-space>
        <el-empty v-else description="暂无并行遥测" :image-size="38" />
      </div>
    </div>
    <el-table v-if="scanStageRows.length" :data="scanStageRows" size="small" max-height="260">
      <el-table-column label="阶段" min-width="190"><template #default="{ row }">{{ scanStageLabel(row.stage) }}</template></el-table-column>
      <el-table-column prop="stage" label="内部键" min-width="220" show-overflow-tooltip />
      <el-table-column label="耗时" width="120"><template #default="{ row }">{{ row.milliseconds }} ms</template></el-table-column>
    </el-table>
    <el-empty v-else description="最新扫描尚未提供阶段遥测" :image-size="48" />
  </el-card>

  <el-card shadow="never" class="section-gap" header="观察池与同花顺看盘">
    <el-table :data="runtime.watchlistItems.value" size="small" max-height="330">
      <el-table-column label="标的" width="120"><template #default="{ row }"><strong>{{ symbolText(row) }}</strong><br/><small>{{ row.label ?? '-' }}</small></template></el-table-column>
      <el-table-column label="提醒" width="130"><template #default="{ row }">入场 {{ row.alert_on_entry ? '开' : '关' }} · 退出 {{ row.alert_on_exit ? '开' : '关' }}</template></el-table-column>
      <el-table-column label="数量/价位" min-width="160"><template #default="{ row }">{{ row.available_quantity ?? '-' }} 股 · {{ row.entry_price ?? '-' }}<br/>止损 {{ row.hard_stop ?? '-' }} · 止盈 {{ row.take_profit ?? '-' }}</template></el-table-column>
      <el-table-column prop="updated_at" label="更新" width="175"><template #default="{ row }">{{ dateText(row.updated_at) }}</template></el-table-column>
      <el-table-column label="操作" width="220" fixed="right"><template #default="{ row }"><el-button size="small" :icon="Search" @click="inspectCard(row)">决策卡</el-button><el-button size="small" :icon="Link" @click="openTonghuashun(row)">同花顺</el-button></template></el-table-column>
    </el-table>
    <el-text type="info">{{ runtime.watchlist.value.notice ?? '观察池只用于研究和提醒范围。' }}</el-text>
  </el-card>

  <el-card shadow="never" class="section-gap" header="最新盘中扫描信号">
    <el-table :data="runtime.signals.value" size="small" max-height="420">
      <el-table-column label="标的" width="115"><template #default="{ row }"><strong>{{ symbolText(row) }}</strong></template></el-table-column>
      <el-table-column prop="signal_type" label="信号" width="105"/><el-table-column prop="severity" label="级别" width="85"/><el-table-column prop="state" label="状态" width="105"/>
      <el-table-column prop="score" label="评分" width="80"/><el-table-column label="观察时间" width="175"><template #default="{ row }">{{ dateText(row.observed_at) }}</template></el-table-column>
      <el-table-column label="风险" min-width="220"><template #default="{ row }">{{ (row.risk_flags ?? []).join('、') || '-' }}</template></el-table-column>
      <el-table-column label="操作" width="220" fixed="right"><template #default="{ row }"><el-button size="small" :icon="Search" @click="inspectCard(row)">决策卡</el-button><el-button size="small" :icon="Link" @click="openTonghuashun(row)">同花顺</el-button></template></el-table-column>
    </el-table>
  </el-card>

  <el-row :gutter="14" class="section-gap">
    <el-col :xs="24" :md="12"><el-card shadow="never" header="策略决策与盘后候选"><el-descriptions :column="1" border size="small"><el-descriptions-item label="决策运行">{{ dateText(runtime.strategyDecision.value.run?.created_at) }}</el-descriptions-item><el-descriptions-item label="盘后完成">{{ dateText(runtime.postClose.value.latest_completed?.updated_at) }}</el-descriptions-item><el-descriptions-item label="候选数">{{ runtime.postClose.value.candidates?.length ?? 0 }}</el-descriptions-item></el-descriptions><el-table :data="runtime.strategyDecision.value.recommendations ?? []" size="small" max-height="240" class="section-gap"><el-table-column prop="rank" label="#" width="45"/><el-table-column label="标的" width="105"><template #default="{ row }">{{ symbolText(row) }}</template></el-table-column><el-table-column prop="decision" label="结论" min-width="130"/><el-table-column prop="score" label="评分" width="80"/><el-table-column label="同花顺" width="95"><template #default="{ row }"><el-button text :icon="Link" @click="openTonghuashun(row)">打开</el-button></template></el-table-column></el-table></el-card></el-col>
    <el-col :xs="24" :md="12"><el-card shadow="never" header="健康与晋级门禁"><el-descriptions :column="1" border size="small"><el-descriptions-item label="信号 7 天">{{ runtime.strategyHealth.value.trigger_frequency?.signals_7d ?? '-' }}</el-descriptions-item><el-descriptions-item label="30m 成熟样本">{{ runtime.strategyHealth.value.outcomes_30m?.matured ?? '-' }}</el-descriptions-item><el-descriptions-item label="校验门禁">{{ runtime.strategyHealth.value.validation_gate?.status ?? '-' }}</el-descriptions-item><el-descriptions-item label="晋级记录">{{ runtime.promotion.value.strategies?.length ?? 0 }} 个策略</el-descriptions-item></el-descriptions><el-alert :title="runtime.strategyHealth.value.notice ?? '策略健康只作研究诊断。'" type="info" :closable="false" class="section-gap"/><el-table :data="runtime.promotion.value.strategies ?? []" size="small" max-height="190"><el-table-column prop="strategy_key" label="策略" min-width="160"/><el-table-column prop="status" label="状态" width="110"/><el-table-column prop="execution_eligible" label="执行资格" width="100"><template #default="{ row }">{{ row.execution_eligible ? '需人工批准' : '否' }}</template></el-table-column></el-table></el-card></el-col>
  </el-row>

  <el-dialog v-model="selectedCardOpen" title="盘中决策卡（只读研究证据）" width="760px" destroy-on-close>
    <el-alert v-if="runtime.decisionCardError.value" :title="runtime.decisionCardError.value" type="error" :closable="false" show-icon />
    <el-skeleton v-else-if="runtime.decisionCardLoading.value" :rows="6" animated />
    <template v-else-if="runtime.decisionCard.value">
      <el-descriptions :column="mobileLayout ? 1 : 3" border size="small"><el-descriptions-item label="标的">{{ runtime.decisionCard.value.symbol }}</el-descriptions-item><el-descriptions-item label="动作">{{ runtime.decisionCard.value.action }}</el-descriptions-item><el-descriptions-item label="可执行">{{ runtime.decisionCard.value.decision_eligible ? '是' : '否' }}</el-descriptions-item><el-descriptions-item label="报价时间">{{ dateText(runtime.decisionCard.value.observed_at) }}</el-descriptions-item><el-descriptions-item label="市场状态">{{ runtime.decisionCard.value.market_state }}</el-descriptions-item><el-descriptions-item label="风险">{{ (runtime.decisionCard.value.risk_flags ?? []).join('、') || '-' }}</el-descriptions-item></el-descriptions>
      <el-alert :title="runtime.decisionCard.value.notice ?? '研究复核卡，不是委托指令。'" type="warning" :closable="false" class="section-gap" />
      <el-button :icon="Link" @click="openCardTonghuashun">在同花顺打开</el-button>
    </template>
    <el-empty v-else description="尚未读取决策卡" :image-size="64" />
  </el-dialog>
</template>

<style scoped>
.scan-performance-visuals {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(240px, 0.75fr);
  gap: 16px;
  margin: 14px 0;
}

.executor-capacity-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 12px;
  margin-top: 14px;
}

.executor-capacity-item {
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.executor-capacity-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 9px;
}

.executor-capacity-item small {
  display: block;
  margin-top: 8px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.resource-capacity-grid {
  margin-top: 14px;
}

.resource-capacity-item {
  height: 100%;
}

.scan-stage-visual,
.scan-overlap-visual {
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.scan-visual-heading,
.scan-stage-visual-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.scan-visual-heading {
  margin-bottom: 12px;
}

.scan-visual-heading small {
  color: var(--el-text-color-secondary);
}

.scan-stage-visual-row + .scan-stage-visual-row {
  margin-top: 10px;
}

.scan-stage-visual-label {
  margin-bottom: 5px;
  color: var(--el-text-color-regular);
  font-size: 12px;
}

.scan-stage-track {
  height: 8px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--el-fill-color-light);
}

.scan-stage-fill {
  display: block;
  min-width: 2px;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--el-color-primary), var(--el-color-success));
}

.scan-overlap-visual :deep(.el-space) {
  width: 100%;
}

@media (max-width: 768px) {
  .scan-performance-visuals {
    grid-template-columns: 1fr;
  }
}
</style>
