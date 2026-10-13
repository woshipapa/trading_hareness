<script setup lang="ts">
import { computed, inject } from 'vue';
import { Refresh } from '@element-plus/icons-vue';
import { targetGroupsContextKey } from '../../dashboard-context';
import { useDeliveryCheck } from '../../composables/useDeliveryCheck';
import type { DeliveryRow } from '../../api/delivery';

const context = inject(targetGroupsContextKey);
if (!context) throw new Error('delivery check requires the target groups context');

const { counts, deliveries, loading, error, load } = useDeliveryCheck(context.selectedTarget);
const selectedTarget = computed(() => context.selectedTarget.value);

type DeliveryState = '已核对' | '未核对' | '已撤回' | '撤回失败';

function stateOf(row: DeliveryRow): DeliveryState {
  if (row.recalled_at) return '已撤回';
  if (row.recall_error) return '撤回失败';
  if (row.target_message_id) return '已核对';
  return '未核对';
}

function stateType(state: DeliveryState): 'success' | 'info' | 'warning' | 'danger' {
  return state === '已核对' ? 'success' : state === '已撤回' ? 'info' : state === '撤回失败' ? 'danger' : 'warning';
}

const shanghaiTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
});

function timeText(value: string | null): string {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '-' : shanghaiTime.format(date).replaceAll('/', '-');
}
</script>

<template>
  <el-card shadow="never" class="section-gap">
    <template #header>
      <div class="card-header">
        <span>送达核对与撤回同步</span>
        <el-button size="small" :icon="Refresh" :loading="loading" :disabled="!selectedTarget" @click="load">刷新</el-button>
      </div>
    </template>

    <div v-if="!selectedTarget">在上面的表里选一个目标群</div>
    <template v-else>
      <el-alert title="目前只核对文字形态的转发消息，富文本和卡片显示为不核对。" type="info" :closable="false" class="section-gap" />
      <el-alert
        v-if="selectedTarget.owned !== true || !selectedTarget.bot_id"
        title="当前目标群未记录为本账号群主或转发机器人，消息无法观察或撤回；如果刚添加，请先点击“识别现有目标群”。"
        type="warning"
        :closable="false"
        class="section-gap"
      />
      <el-space wrap class="section-gap">
        <el-tag type="success">已核对 {{ counts.matched }}</el-tag>
        <el-tag type="warning">未核对 {{ counts.unmatched }}</el-tag>
        <el-tag type="info">已撤回 {{ counts.recalled }}</el-tag>
        <el-tag type="danger">撤回失败 {{ counts.recall_failed }}</el-tag>
      </el-space>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="section-gap" />
      <el-table :data="deliveries" :loading="loading" size="small" empty-text="暂无送达记录">
        <el-table-column label="发送时间" min-width="170"><template #default="{ row }">{{ timeText(row.sent_at) }}</template></el-table-column>
        <el-table-column label="状态" width="105"><template #default="{ row }"><el-tag size="small" :type="stateType(stateOf(row))">{{ stateOf(row) }}</el-tag></template></el-table-column>
        <el-table-column prop="target_message_id" label="核对到的消息编号" min-width="190"><template #default="{ row }">{{ row.target_message_id || '-' }}</template></el-table-column>
        <el-table-column label="撤回时间" min-width="170"><template #default="{ row }">{{ timeText(row.recalled_at) }}</template></el-table-column>
        <el-table-column prop="recall_error" label="失败原因" min-width="220" show-overflow-tooltip><template #default="{ row }">{{ row.recall_error || '-' }}</template></el-table-column>
      </el-table>
    </template>
  </el-card>
</template>
