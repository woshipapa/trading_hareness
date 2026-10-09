<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { Refresh } from '@element-plus/icons-vue';
import { getJson } from '../api/http';
import {
  STATUS_LABELS, VERDICT_LABELS, VERDICT_TYPES, activeCapabilityCount, ageText, failingHealthCount,
  type DatasourceBoard,
} from '../research/boards';

const board = ref<DatasourceBoard | null>(null);
const loading = ref(false);
const error = ref('');

async function load() {
  loading.value = true;
  error.value = '';
  try {
    board.value = await getJson<DatasourceBoard>('/api/research/datasources/board');
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <el-card shadow="never" class="section-gap">
    <template #header>
      <div class="card-header">
        <el-space wrap>
          <strong>数据源能力与健康</strong>
          <el-tag v-for="(count, verdict) in board?.summary ?? {}" :key="verdict" size="small" :type="VERDICT_TYPES[verdict] ?? 'info'">
            {{ VERDICT_LABELS[verdict] ?? verdict }} {{ count }}
          </el-tag>
          <el-text type="info" size="small">{{ board?.in_session ? '盘中' : '非交易时段' }} · {{ board ? ageText(board.observed_at) : '' }}</el-text>
        </el-space>
        <el-button :icon="Refresh" :loading="loading" size="small" @click="load">刷新</el-button>
      </div>
    </template>
    <el-alert title="按数据源目录归组：每个来源服务哪些能力、优先级（开盘啦在前）与状态，以及健康表里它的每项调用。已退役来源只作历史，不告警；“陈旧”指盘中实时类来源 15 分钟、其他来源 3 天内没有成功。" type="info" :closable="false" show-icon />
    <el-alert v-if="error" :title="`读取失败：${error}`" type="error" :closable="false" show-icon class="section-gap" />
    <div v-if="board?.alerts?.length" class="section-gap">
      <el-alert v-for="alert in board.alerts" :key="alert.key" :title="`${alert.label}（${alert.key}）：${VERDICT_LABELS[alert.verdict] ?? alert.verdict}`"
        :description="alert.reasons.join('；')" type="error" :closable="false" show-icon class="alert-row" />
    </div>
    <el-table :data="board?.sources ?? []" size="small" max-height="620" class="section-gap" row-key="key">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="expand">
            <el-text v-if="row.risks" size="small" type="warning">风险：{{ row.risks }}</el-text>
            <el-table :data="row.capabilities" size="small" class="section-gap" empty-text="目录中没有绑定">
              <el-table-column prop="label" label="能力" min-width="150" />
              <el-table-column prop="capability" label="键" min-width="150" />
              <el-table-column prop="priority" label="优先级" width="70" />
              <el-table-column label="状态" width="110"><template #default="{ row: cap }">{{ STATUS_LABELS[cap.status] ?? cap.status }}</template></el-table-column>
              <el-table-column prop="notes" label="说明" min-width="260" show-overflow-tooltip />
            </el-table>
            <el-table :data="row.health" size="small" class="section-gap" empty-text="没有健康记录">
              <el-table-column prop="capability" label="健康记录（调用）" min-width="170" />
              <el-table-column label="最近成功" width="110"><template #default="{ row: item }">{{ ageText(item.last_success_at) }}</template></el-table-column>
              <el-table-column label="连续失败" width="80"><template #default="{ row: item }">{{ item.consecutive_failures ?? 0 }}</template></el-table-column>
              <el-table-column label="熔断" width="70"><template #default="{ row: item }"><el-tag v-if="item.circuit_open" size="small" type="danger">是</el-tag><span v-else>-</span></template></el-table-column>
              <el-table-column prop="last_latency_ms" label="延迟ms" width="80" />
              <el-table-column prop="last_row_count" label="行数" width="70" />
              <el-table-column prop="last_error" label="最近错误" min-width="220" show-overflow-tooltip />
            </el-table>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="数据源" min-width="190">
        <template #default="{ row }"><div>{{ row.label }}</div><el-text size="small" type="info">{{ row.key }}</el-text></template>
      </el-table-column>
      <el-table-column label="判定" width="96">
        <template #default="{ row }">
          <el-tooltip :content="row.reasons.join('；') || '—'" placement="top">
            <el-tag size="small" :type="VERDICT_TYPES[row.verdict] ?? 'info'">{{ VERDICT_LABELS[row.verdict] ?? row.verdict }}</el-tag>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column label="在用能力" width="82"><template #default="{ row }">{{ activeCapabilityCount(row) }}</template></el-table-column>
      <el-table-column label="异常调用" width="82">
        <template #default="{ row }"><span :class="{ warn: failingHealthCount(row) }">{{ failingHealthCount(row) }} / {{ row.health.length }}</span></template>
      </el-table-column>
      <el-table-column label="最近成功" width="110"><template #default="{ row }">{{ ageText(row.last_success_at) }}</template></el-table-column>
      <el-table-column prop="order" label="优先级" width="70" />
      <el-table-column label="接入" min-width="150"><template #default="{ row }">{{ [row.protocol, row.license, row.cost].filter(Boolean).join(' · ') }}</template></el-table-column>
    </el-table>
  </el-card>
</template>

<style scoped>
.expand { padding: 4px 12px 8px 48px; }
.alert-row { margin-bottom: 6px; }
.warn { color: #d93026; font-weight: 600; }
</style>
