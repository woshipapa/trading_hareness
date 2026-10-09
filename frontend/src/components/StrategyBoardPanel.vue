<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { Refresh } from '@element-plus/icons-vue';
import { getJson } from '../api/http';
import {
  READINESS_LABELS, READINESS_TYPES, STATUS_LABELS, VERDICT_LABELS, VERDICT_TYPES, ageText, ledgerText,
  type StrategyBoard, type StrategyInput,
} from '../research/boards';

const board = ref<StrategyBoard | null>(null);
const loading = ref(false);
const error = ref('');

async function load() {
  loading.value = true;
  error.value = '';
  try {
    board.value = await getJson<StrategyBoard>('/api/research/strategy/board');
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason);
  } finally {
    loading.value = false;
  }
}

function fallbackText(input: StrategyInput): string {
  return (input.fallbacks ?? []).map((item) => `${item.source}（${VERDICT_LABELS[item.verdict ?? ''] ?? item.verdict ?? '-'}）`).join('、') || '—';
}

onMounted(load);
</script>

<template>
  <el-card shadow="never" class="section-gap">
    <template #header>
      <div class="card-header">
        <el-space wrap>
          <strong>量化策略看板</strong>
          <el-tag v-for="(count, readiness) in board?.summary ?? {}" :key="readiness" size="small" :type="READINESS_TYPES[readiness] ?? 'info'">
            {{ READINESS_LABELS[readiness] ?? readiness }} {{ count }}
          </el-tag>
          <el-text type="info" size="small">{{ board ? ageText(board.observed_at) : '' }}</el-text>
        </el-space>
        <el-button :icon="Refresh" :loading="loading" size="small" @click="load">刷新</el-button>
      </div>
    </template>
    <el-alert title="每个注册策略声明自己需要的数据能力；这里给出每项能力由哪个数据源提供、该源此刻是否健康。必需输入的来源熔断、失败、陈旧或已退役时，策略标为“输入受阻”。所有策略 live_effect=none，只产出研究证据。" type="info" :closable="false" show-icon />
    <el-alert v-if="error" :title="`读取失败：${error}`" type="error" :closable="false" show-icon class="section-gap" />
    <el-table :data="board?.strategies ?? []" size="small" max-height="620" class="section-gap" row-key="key">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="expand">
            <el-text size="small">{{ row.description }}</el-text>
            <el-table :data="row.inputs" size="small" class="section-gap" empty-text="未声明数据需求">
              <el-table-column prop="label" label="需要的能力" min-width="150" />
              <el-table-column label="必需" width="60"><template #default="{ row: input }">{{ input.required ? '是' : '否' }}</template></el-table-column>
              <el-table-column label="首选来源" min-width="140"><template #default="{ row: input }">{{ input.source ?? '无' }}<el-text size="small" type="info"> {{ STATUS_LABELS[input.binding_status] ?? '' }}</el-text></template></el-table-column>
              <el-table-column label="来源健康" width="96"><template #default="{ row: input }"><el-tag size="small" :type="VERDICT_TYPES[input.source_verdict] ?? 'info'">{{ VERDICT_LABELS[input.source_verdict] ?? input.source_verdict }}</el-tag></template></el-table-column>
              <el-table-column label="备选" min-width="160"><template #default="{ row: input }">{{ fallbackText(input) }}</template></el-table-column>
              <el-table-column prop="purpose" label="用途" min-width="220" show-overflow-tooltip />
            </el-table>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="策略" min-width="210">
        <template #default="{ row }"><div>{{ row.key }}<el-tag v-if="row.deprecated" size="small" type="info"> 已弃用</el-tag></div><el-text size="small" type="info">{{ row.model_version }}</el-text></template>
      </el-table-column>
      <el-table-column label="成熟度" width="110"><template #default="{ row }"><el-tag size="small" effect="plain">{{ row.maturity }}</el-tag></template></el-table-column>
      <el-table-column label="数据就绪" width="104">
        <template #default="{ row }"><el-tag size="small" :type="READINESS_TYPES[row.readiness] ?? 'info'">{{ READINESS_LABELS[row.readiness] ?? row.readiness }}</el-tag></template>
      </el-table-column>
      <el-table-column label="受阻 / 偏弱的输入" min-width="260" show-overflow-tooltip>
        <template #default="{ row }">{{ [...row.blocking_inputs, ...row.weak_inputs].join('；') || '—' }}</template>
      </el-table-column>
      <el-table-column label="台账线（最近候选）" min-width="220" show-overflow-tooltip><template #default="{ row }">{{ ledgerText(row) }}</template></el-table-column>
      <el-table-column label="提醒" width="110"><template #default="{ row }">{{ row.alert_effect }}</template></el-table-column>
    </el-table>
  </el-card>
</template>

<style scoped>
.expand { padding: 4px 12px 8px 48px; }
</style>
