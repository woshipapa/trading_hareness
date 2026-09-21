<script lang="ts">
import { defineComponent, inject } from 'vue';
import { Refresh } from '@element-plus/icons-vue';
import { groupRelayMonitorContextKey } from '../dashboard-context';

export default defineComponent({
  name: 'GroupRelayMonitorView',
  setup() {
    const dashboard = inject(groupRelayMonitorContextKey);
    if (!dashboard) throw new Error('group relay monitor requires the dashboard shell context');
    return { ...dashboard, Refresh };
  },
});
</script>

<template>
  <el-card shadow="never" class="group-relay-status-panel">
    <template #header><div class="card-header"><div><span>群消息转发状态</span><small class="realtime-refresh-time">每 10 秒自动刷新；“最近源消息”与“最近成功轮询”分开显示。</small></div><el-space><el-button size="small" :icon="Refresh" :loading="groupRelayLoading" @click="loadGroupRelayStatus">刷新</el-button><el-button size="small" type="primary" @click="openCreateGroupRelayRoute">新增源群</el-button></el-space></div></template>
    <el-alert v-if="groupRelayError" :title="groupRelayError" type="error" :closable="false" show-icon />
    <template v-else>
      <el-descriptions :column="mobileLayout ? 1 : 5" border size="small">
        <el-descriptions-item label="整体状态"><el-tag :type="groupRelayStateType(groupRelayStatus.status)">{{ groupRelayStateText(groupRelayStatus.status) }}</el-tag></el-descriptions-item>
        <el-descriptions-item label="轮询间隔">{{ groupRelayStatus.interval_seconds ?? '-' }} 秒</el-descriptions-item>
        <el-descriptions-item label="用户读取授权"><el-tag :type="groupRelayStatus.user_oauth_configured ? oauthAuditTagType(groupRelayStatus.user_oauth_scope_audit) : 'danger'">{{ groupRelayStatus.user_oauth_configured ? oauthAuditLabel(groupRelayStatus.user_oauth_scope_audit) : '未配置' }}</el-tag></el-descriptions-item>
        <el-descriptions-item label="最近全局轮询">{{ dateText(groupRelayStatus.last_tick_completed_at) }}</el-descriptions-item>
        <el-descriptions-item label="写入端"><el-tag :type="groupRelayStateType(groupRelayStatus.writer?.state)">{{ groupRelayStateText(groupRelayStatus.writer?.state) }}</el-tag><div class="group-relay-age">{{ groupRelayStatus.writer?.owner_id ?? groupRelayStatus.writer?.configured_id ?? '未声明' }} · generation {{ groupRelayStatus.writer?.generation ?? '-' }}</div></el-descriptions-item>
        <el-descriptions-item label="远端投递队列"><el-tag :type="groupRelayStatus.delivery_outbox?.failed ? 'danger' : groupRelayStatus.delivery_outbox?.depth ? 'warning' : 'success'">待投递 {{ groupRelayStatus.delivery_outbox?.depth ?? 0 }} · 终止失败 {{ groupRelayStatus.delivery_outbox?.failed ?? 0 }}</el-tag><div v-if="groupRelayStatus.delivery_outbox?.paused" class="group-relay-age">按操作暂停 {{ groupRelayStatus.delivery_outbox.paused }}（不自动重试）</div></el-descriptions-item>
        <el-descriptions-item label="旧 OAuth 汇总轮询"><el-tag :type="groupRelayStateType(groupRelayStatus.summary_listener?.state)">{{ groupRelayStatus.summary_listener?.enabled ? groupRelayStateText(groupRelayStatus.summary_listener?.state) : '已关闭（实时走 WS）' }}</el-tag><div class="group-relay-age">不参与实时监听，避免 OAuth 轮询额度消耗</div></el-descriptions-item>
      </el-descriptions>
      <el-card shadow="never" class="section-gap group-relay-status-panel">
        <template #header><div class="card-header"><span>LarkAgentX WebSocket 监听</span><el-tag :type="larkAgentXStateType(groupRelayStatus.larkagentx?.websocket?.state)">{{ larkAgentXStateText(groupRelayStatus.larkagentx?.websocket?.state) }}</el-tag></div></template>
        <el-descriptions :column="mobileLayout ? 1 : 6" border size="small">
          <el-descriptions-item label="连接状态"><el-tag :type="larkAgentXStateType(groupRelayStatus.larkagentx?.status)">{{ larkAgentXStateText(groupRelayStatus.larkagentx?.status) }}</el-tag></el-descriptions-item>
          <el-descriptions-item label="监听群数">{{ groupRelayStatus.larkagentx?.listen_chat_count ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="已接收 / 已转发">{{ groupRelayStatus.larkagentx?.observed_count ?? 0 }} / {{ groupRelayStatus.larkagentx?.forwarded_count ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="解码错误"><el-tag :type="groupRelayStatus.larkagentx?.decode_error_count ? 'danger' : 'success'">{{ groupRelayStatus.larkagentx?.decode_error_count ?? 0 }}</el-tag></el-descriptions-item>
          <el-descriptions-item label="事件队列">待处理 {{ groupRelayStatus.larkagentx?.event_spool?.pending ?? 0 }} · 失败 {{ groupRelayStatus.larkagentx?.event_spool?.failed ?? 0 }}</el-descriptions-item>
          <el-descriptions-item label="缺口补读">{{ groupRelayStatus.larkagentx?.gap_repair_enabled ? '已启用' : '已关闭' }}</el-descriptions-item>
        </el-descriptions>
        <el-table :data="Object.entries(groupRelayStatus.larkagentx?.chat_stats ?? {}).map(([chatId, stats]) => ({ chatId, stats, validation: groupRelayStatus.larkagentx?.chat_validation?.[chatId] }))" size="small" max-height="300" class="section-gap">
          <el-table-column prop="chatId" label="WebSocket chat_id" min-width="190"/>
          <el-table-column label="群名/核验" min-width="175"><template #default="{ row }"><div>{{ row.validation?.name || '未返回群名' }}</div><el-tag size="small" :type="larkAgentXStateType(row.validation?.state)">{{ larkAgentXStateText(row.validation?.state) }}</el-tag></template></el-table-column>
          <el-table-column label="接收 / 转发 / 失败" width="150"><template #default="{ row }">{{ row.stats?.observed_count ?? 0 }} / {{ row.stats?.forwarded_count ?? 0 }} / {{ row.stats?.failed_count ?? 0 }}</template></el-table-column>
          <el-table-column label="最近消息" min-width="170"><template #default="{ row }"><div>{{ dateText(row.stats?.last_observed_at) }}</div><small class="group-relay-age">{{ row.stats?.last_message_type || '-' }}</small></template></el-table-column>
          <el-table-column label="最近转发" min-width="170"><template #default="{ row }">{{ dateText(row.stats?.last_forwarded_at) }}</template></el-table-column>
        </el-table>
      </el-card>
      <el-card shadow="never" class="section-gap">
        <template #header><div class="card-header"><span>Webhook 目标与关键词</span><el-tag :type="groupRelayStatus.webhook_config?.all_webhook_keywords_loaded ? 'success' : 'danger'">{{ groupRelayStatus.webhook_config?.all_webhook_keywords_loaded ? '关键词已全部加载' : '存在未匹配目标' }}</el-tag></div></template>
        <el-table :data="groupRelayStatus.webhook_config?.keyword_entries ?? []" size="small" max-height="220">
          <el-table-column prop="chat_id" label="目标 chat_id" min-width="240"/>
          <el-table-column prop="keyword" label="Webhook keyword" min-width="140"/>
          <el-table-column label="状态" width="120"><template #default="{ row }"><el-tag size="small" :type="groupRelayStatus.webhook_config?.webhook_chat_ids?.includes(row.chat_id) ? 'success' : 'danger'">{{ groupRelayStatus.webhook_config?.webhook_chat_ids?.includes(row.chat_id) ? 'URL 已加载' : '缺少 URL' }}</el-tag></template></el-table-column>
        </el-table>
        <el-alert v-if="(groupRelayStatus.webhook_config?.missing_keyword_chat_ids?.length ?? 0) > 0" class="section-gap" type="error" :closable="false" :title="`缺少关键词：${groupRelayStatus.webhook_config?.missing_keyword_chat_ids?.join('、')}`"/>
      </el-card>
      <el-table :data="groupRelayStatus.sources ?? []" size="small" max-height="390" class="section-gap group-relay-table">
        <el-table-column prop="chat_name" label="源群" min-width="190" show-overflow-tooltip/>
        <el-table-column label="源 chat_id" min-width="190" show-overflow-tooltip><template #default="{ row }">{{ row.source_chat_id || '-' }}</template></el-table-column>
        <el-table-column label="标签" width="110"><template #default="{ row }"><el-tag size="small" effect="plain">#{{ row.tag }}</el-tag></template></el-table-column>
        <el-table-column label="目标 / keyword" min-width="220"><template #default="{ row }"><div v-for="target in row.target_chat_ids ?? []" :key="target">{{ target }}</div><small v-for="target in row.target_chat_ids ?? []" :key="`${target}-keyword`" class="group-relay-age">#{{ webhookKeyword(target) }}</small></template></el-table-column>
        <el-table-column label="监听状态" width="118"><template #default="{ row }"><el-tag size="small" :type="groupRelayStateType(row.state)">{{ groupRelayStateText(row.state) }}</el-tag></template></el-table-column>
        <el-table-column label="实时传输" min-width="165"><template #default="{ row }"><el-tag size="small" :type="row.transport === 'larkagentx_websocket' ? 'success' : 'warning'">{{ row.transport === 'larkagentx_websocket' ? 'LarkAgentX WS' : row.transport === 'oauth_poll' ? 'OAuth 轮询' : '未启用' }}</el-tag><div v-for="chatId in row.websocket_chat_ids ?? []" :key="chatId" class="group-relay-age">WS {{ chatId }}</div></template></el-table-column>
        <el-table-column label="最近接收" min-width="175"><template #default="{ row }"><div>{{ dateText(row.websocket_last_observed_at || row.last_source_message_at) }}</div><small class="group-relay-age">WS 接收 {{ row.websocket_observed_count ?? 0 }} · 旧轮询 {{ ageText(row.poll_age_seconds) }} 前</small></template></el-table-column>
        <el-table-column label="投递验收" min-width="145"><template #default="{ row }"><el-tag size="small" :type="relayDeliveryTagType(row.delivery_state)">{{ relayDeliveryLabel(row.delivery_state) }}</el-tag><div v-if="row.last_forwarded_at" class="group-relay-age">{{ dateText(row.last_forwarded_at) }}</div></template></el-table-column>
        <el-table-column label="n8n / 远端" min-width="160"><template #default="{ row }"><el-tag size="small" :type="ingestionDeliveryTagType(row.ingestion)">{{ ingestionDeliveryLabel(row.ingestion) }}</el-tag><div v-if="row.ingestion?.last_updated_at" class="group-relay-age">{{ dateText(row.ingestion.last_updated_at) }}</div></template></el-table-column>
        <el-table-column label="编辑/撤回对账" min-width="155"><template #default="{ row }">{{ dateText(row.last_reconciled_at) }}</template></el-table-column>
        <el-table-column label="失败待重试" width="105"><template #default="{ row }"><el-tag size="small" :type="row.failed_count ? 'danger' : 'success'">{{ row.failed_count ?? 0 }}</el-tag></template></el-table-column>
        <el-table-column label="最近错误" min-width="190" show-overflow-tooltip><template #default="{ row }">{{ row.last_error || '-' }}</template></el-table-column>
        <el-table-column label="已恢复错误" min-width="190" show-overflow-tooltip><template #default="{ row }"><div>{{ row.last_resolved_error || '-' }}</div><small v-if="row.last_resolved_error_at" class="group-relay-age">{{ dateText(row.last_resolved_error_at) }}</small></template></el-table-column>
        <el-table-column label="管理" width="170" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="openEditGroupRelayRoute(row)">编辑</el-button><el-button link :type="row.enabled === false ? 'success' : 'warning'" @click="setGroupRelayRouteEnabled(row, row.enabled === false)">{{ row.enabled === false ? '启用' : '停用' }}</el-button><el-button link type="danger" @click="deleteGroupRelayRoute(row)">删除</el-button></template></el-table-column>
      </el-table>
    </template>
  </el-card>
  <el-card shadow="never"><template #header><div class="card-header"><span>导入事件</span><el-select v-model="eventFilter" size="small" class="event-filter"><el-option label="全部状态" value="all"/><el-option label="已完成" value="已完成"/><el-option label="失败" value="失败"/><el-option label="处理中" value="已接收，处理中"/></el-select></div></template><el-empty v-if="!visibleEvents.length" description="暂无事件"/><el-timeline v-else><el-timeline-item v-for="event in visibleEvents" :key="event.event_id" :timestamp="dateText(event.received_at)" :type="event.n8n_status === '失败' ? 'danger' : 'primary'"><el-card shadow="never"><div class="event-title"><strong>{{ event.message_type || 'message' }}</strong><el-tag size="small">{{ event.n8n_status || '未知' }}</el-tag></div><p v-if="event.text">{{ event.text }}</p><el-text type="info">{{ event.source_label || '无来源备注' }}{{ event.n8n_error ? ` · ${event.n8n_error}` : '' }}</el-text></el-card></el-timeline-item></el-timeline></el-card>
</template>
