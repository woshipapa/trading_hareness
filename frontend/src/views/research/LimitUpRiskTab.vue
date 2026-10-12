<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { Refresh } from '@element-plus/icons-vue';
import VChart from 'vue-echarts';
import { getJson } from '../../api/http';
import { shanghaiToday } from '../../research/session-radar';
import {
  percent, sortedRows, trackOption, yuanYi,
  type LimitUpRiskDaily, type LimitUpRiskReading, type LimitUpRiskTrack,
} from '../../research/limit-up-risk';

const tradeDate = ref(shanghaiToday());
const daily = ref<LimitUpRiskDaily | null>(null);
const track = ref<LimitUpRiskTrack | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

async function loadDaily(day?: string) {
  daily.value = await getJson<LimitUpRiskDaily>(`/api/research/market/limit-up-risk/daily${day ? `?date=${encodeURIComponent(day)}` : ''}`);
  if (!day && daily.value.reading) tradeDate.value = daily.value.reading.trade_date;
}

async function load() {
  loading.value = true;
  error.value = null;
  try { await Promise.all([loadDaily(), getJson<LimitUpRiskTrack>('/api/research/market/limit-up-risk/track?days=250').then((value) => { track.value = value; })]); }
  catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause); }
  finally { loading.value = false; }
}

onMounted(() => { void load(); });
watch(tradeDate, (day) => {
  if (daily.value?.reading?.trade_date === day) return;
  daily.value = null;
  error.value = null;
  void loadDaily(day).catch((cause) => {
    daily.value = null;
    error.value = cause instanceof Error ? cause.message : String(cause);
  });
});

const reading = computed<LimitUpRiskReading | null>(() => daily.value?.reading ?? null);
const rows = computed(() => sortedRows(reading.value));
const chart = computed(() => (track.value ? trackOption(track.value) : null));
const recent = computed(() => track.value?.recent ?? null);
const statusLabel = computed(() => recent.value?.status === 'working' ? '近期有效' : recent.value?.status === 'not_working_recently' ? '近期无效' : '历史不足');
</script>

<template>
  <section class="risk">
    <div class="toolbar">
      <el-date-picker v-model="tradeDate" type="date" value-format="YYYY-MM-DD" size="small" :clearable="false" />
      <el-button :icon="Refresh" :loading="loading" size="small" @click="load">刷新</el-button>
      <el-tag type="warning" effect="plain">研究用途，不构成操作建议</el-tag>
      <span class="caption">{{ daily?.version ?? track?.version ?? '-' }}</span>
    </div>
    <el-alert v-if="error" :title="error" type="warning" show-icon :closable="false" class="gap" />
    <el-card v-if="reading" shadow="never" class="gap">
      <template #header>{{ reading.trade_date }} 涨停股大面风险分 · {{ reading.list_source === 'history' ? '历史榜单' : '实时榜单' }} · {{ reading.rows.length }} 只</template>
      <el-table :data="rows" size="small" stripe>
        <el-table-column prop="name" label="名称" min-width="100" />
        <el-table-column prop="boards" label="连板" width="72" />
        <el-table-column prop="first_seal_time" label="首次封板" width="96" />
        <el-table-column label="开盘即封" width="92"><template #default="{ row }">{{ row.sealed_from_open ? '是' : '否' }}</template></el-table-column>
        <el-table-column label="封成比" width="92"><template #default="{ row }">{{ percent(row.seal_ratio) }}</template></el-table-column>
        <el-table-column label="主力净额占比" width="112"><template #default="{ row }">{{ percent(row.flow) }}</template></el-table-column>
        <el-table-column label="量比" width="78"><template #default="{ row }">{{ row.vr.toFixed(2) }}</template></el-table-column>
        <el-table-column label="流通市值（亿）" width="116"><template #default="{ row }">{{ yuanYi(row.float_cap) }}</template></el-table-column>
        <el-table-column label="风险排名" prop="risk_rank" width="92"><template #default="{ row }">{{ row.risk_rank.toFixed(1) }}</template></el-table-column>
      </el-table>
      <div class="caption coverage">覆盖：{{ reading.ranks_reliable ? '排名可靠' : '样本少于 20，排名仅供参考' }} · {{ Object.entries(reading.coverage).map(([key, value]) => `${key} ${value}`).join(' · ') }}</div>
    </el-card>
    <el-empty v-else description="该日期没有落库的风险分" class="gap" />

    <el-card shadow="never" class="gap">
      <template #header>最近 250 个交易日的次日大面表现（20 日平滑）</template>
      <v-chart v-if="chart && track?.days.length" :option="chart" autoresize class="chart" />
      <el-empty v-else description="还没有足够的表现记录" />
    </el-card>
    <el-card v-if="recent" shadow="never" class="gap recent">
      <span>近期状态：<el-tag :type="recent.status === 'working' ? 'success' : recent.status === 'insufficient_history' ? 'info' : 'danger'">{{ statusLabel }}</el-tag></span>
      <span>最近 {{ recent.sessions }} 个交易日</span>
      <span>最低风险五分之一 {{ percent(recent.lowest_big_loss_rate) }}</span>
      <span>最高风险五分之一 {{ percent(recent.highest_big_loss_rate) }}</span>
      <span>差 {{ recent.difference_points?.toFixed(1) ?? '-' }} 个百分点</span>
      <span>日聚类 t={{ recent.t_value?.toFixed(2) ?? '—' }}</span>
    </el-card>
  </section>
</template>

<style scoped>
.toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 12px; }
.caption { color: #8a8f98; font-size: 12px; }
.gap { margin-top: 12px; }
.coverage { margin-top: 8px; }
.chart { height: 380px; width: 100%; }
.recent { display: flex; flex-wrap: wrap; gap: 18px; align-items: center; line-height: 1.8; }
</style>
