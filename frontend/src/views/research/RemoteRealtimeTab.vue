<script setup lang="ts">
import { computed, inject, ref } from 'vue';
import { Refresh, Link, Search } from '@element-plus/icons-vue';
import { dashboardContextKey } from '../../dashboard-context';
import { useRemoteQuantRuntime } from '../../composables/useRemoteQuantRuntime';
import { freshnessOf, freshnessLabel, symbolFromRow } from '../../research/remote-runtime';

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
const healthState = (value: unknown) => value === 'healthy' || value === 'ready' ? 'success' : ['degraded', 'unavailable', 'disabled'].includes(String(value)) ? 'danger' : 'warning';
const liveEffect = computed(() => runtime.strategyHealth.value.validation_gate?.live_effect ?? runtime.promotion.value.live_effect ?? 'none');
</script>

<template>
  <el-alert title="远端 47 owner 只读观察台：实时数据、盘中扫描和策略/复盘结果来自已落库证据。所有结果保持研究模式，不会自动下单。" type="warning" show-icon :closable="false" class="section-gap" />
  <el-card shadow="never" class="section-gap">
    <template #header><div class="card-header"><span>Owner 运行状态</span><el-button text :icon="Refresh" :loading="runtime.loading.value" @click="runtime.loadRemoteRuntimeCore">刷新</el-button></div></template>
    <el-alert v-if="runtime.error.value" :title="runtime.error.value" type="warning" :closable="false" show-icon class="section-gap" />
    <el-descriptions :column="mobileLayout ? 1 : 5" border size="small">
      <el-descriptions-item label="客户端读取">{{ runtime.lastSuccessAt.value ? dateText(runtime.lastSuccessAt.value) : '尚未成功读取' }}</el-descriptions-item>
      <el-descriptions-item label="远端网络">{{ runtime.runtimeHealth.value.network?.state ?? '-' }}</el-descriptions-item>
      <el-descriptions-item label="运行 SHA">{{ runtime.runtimeHealth.value.build?.running_sha ?? runtime.runtimeHealth.value.build?.git_sha ?? '-' }}</el-descriptions-item>
      <el-descriptions-item label="发布">{{ runtime.runtimeHealth.value.build?.release ?? '-' }}</el-descriptions-item>
      <el-descriptions-item label="策略 live_effect"><el-tag type="warning">{{ liveEffect }}</el-tag></el-descriptions-item>
    </el-descriptions>
    <el-space wrap class="section-gap"><el-tag v-for="item in runtime.services.value.items ?? []" :key="item.key" :type="healthState(item.state)">{{ item.label ?? item.key }}：{{ item.state ?? '-' }}</el-tag></el-space>
  </el-card>

  <el-row :gutter="14" class="section-gap">
    <el-col :xs="12" :md="6"><el-card shadow="never" class="metric-card"><span>观察池</span><strong>{{ runtime.watchlistItems.value.length }}</strong></el-card></el-col>
    <el-col :xs="12" :md="6"><el-card shadow="never" class="metric-card"><span>最新信号</span><strong>{{ runtime.signals.value.length }}</strong></el-card></el-col>
    <el-col :xs="12" :md="6"><el-card shadow="never" class="metric-card"><span>扫描时间</span><strong class="metric-text">{{ dateText(runtime.latestScan.value.scan?.observed_at) }}</strong></el-card></el-col>
    <el-col :xs="12" :md="6"><el-card shadow="never" class="metric-card"><span>扫描新鲜度</span><strong><el-tag :type="scanFreshness === 'fresh' ? 'success' : 'warning'">{{ freshnessLabel(scanFreshness) }}</el-tag></strong></el-card></el-col>
  </el-row>

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
