<script setup lang="ts">
import { DataAnalysis, Document, Operation, Promotion, Refresh, UploadFilled } from '@element-plus/icons-vue';
import GroupRelayMonitorView from './views/GroupRelayMonitorView.vue';
import FeishuWorkbenchView from './views/FeishuWorkbenchView.vue';
import ManualRelayView from './views/ManualRelayView.vue';
import SendView from './views/SendView.vue';
import { useFeishuDashboardWorkspace } from './composables/useFeishuDashboardWorkspace';

const dashboard = useFeishuDashboardWorkspace();
</script>

<template>
  <el-container class="app-shell">
    <el-aside width="236px" class="side-nav">
      <div class="brand"><el-icon><DataAnalysis /></el-icon><div><strong>Feishu Relay</strong><span>实时监听与消息投递</span></div></div>
      <el-menu :default-active="dashboard.activeSection" class="menu" @select="dashboard.selectActiveSection">
        <el-menu-item index="monitor"><el-icon><Operation /></el-icon><span>群监听状态</span></el-menu-item>
        <el-menu-item index="workbench"><el-icon><Document /></el-icon><span>飞书工作台</span></el-menu-item>
        <el-menu-item index="relay"><el-icon><UploadFilled /></el-icon><span>手动投递</span></el-menu-item>
        <el-menu-item index="send"><el-icon><Promotion /></el-icon><span>发送</span></el-menu-item>
      </el-menu>
      <div class="side-state"><el-tag :type="dashboard.connected ? 'success' : 'warning'" effect="plain">{{ dashboard.connected ? '事件流已连接' : '事件流重连中' }}</el-tag></div>
    </el-aside>
    <el-container>
      <el-header class="topbar"><div><h1>{{ dashboard.activeSection === 'monitor' ? '群监听状态' : dashboard.activeSection === 'workbench' ? '飞书工作台' : dashboard.activeSection === 'send' ? '发送' : '手动投递' }}</h1><span>{{ dashboard.activeSection === 'monitor' ? 'LarkAgentX WebSocket、webhook 和 Itougu 状态' : dashboard.activeSection === 'workbench' ? '汇总群协作闭环、能力与授权状态' : dashboard.activeSection === 'send' ? '个人会话统一发送:限速、幂等、可审计' : '通过适配器提交人工消息和媒体' }}</span></div><el-button :icon="Refresh" :loading="dashboard.activeSection === 'monitor' ? dashboard.groupRelayLoading : dashboard.feishuWorkbenchLoading" @click="dashboard.activeSection === 'monitor' ? dashboard.loadGroupRelayStatus() : dashboard.loadFeishuWorkbench()">刷新数据</el-button></el-header>
      <el-main class="content">
        <GroupRelayMonitorView v-if="dashboard.activeSection === 'monitor'" />
        <FeishuWorkbenchView v-else-if="dashboard.activeSection === 'workbench'" />
        <SendView v-else-if="dashboard.activeSection === 'send'" />
        <ManualRelayView v-else />
      </el-main>
    </el-container>
  </el-container>

  <el-dialog v-model="dashboard.groupRelayRouteDialog" :title="dashboard.groupRelayRouteForm.key ? '编辑源群' : '新增源群'" width="520px" destroy-on-close>
    <el-form label-position="top" @submit.prevent="dashboard.saveGroupRelayRoute">
      <el-alert title="从本账号已加入的群里选源群，保存后立即开始监听，并在后台读取该群的全部历史。" type="info" :closable="false" show-icon class="section-gap" />
      <el-alert v-if="dashboard.joinedGroupsError" :title="`群列表暂时读不到：${dashboard.joinedGroupsError}。仍可直接输入群名保存。`" type="warning" :closable="false" show-icon class="section-gap" />
      <el-form-item label="源群" required>
        <el-select :model-value="dashboard.groupRelayRouteForm.websocket_chat_id || dashboard.groupRelayRouteForm.chat_name" filterable allow-create default-first-option :loading="dashboard.joinedGroupsLoading" placeholder="选择已加入的群，或输入群名" style="width: 100%" @change="dashboard.pickRouteGroup">
          <el-option v-for="group in dashboard.pickableGroups" :key="group.chat_id" :value="group.chat_id" :label="dashboard.groupOptionLabel(group)" :disabled="Boolean(group.source_key) && group.source_key !== dashboard.groupRelayRouteForm.key" />
        </el-select>
      </el-form-item>
      <el-form-item label="转发标签" required><el-input v-model="dashboard.groupRelayRouteForm.tag" maxlength="32" placeholder="例如：quanneng"><template #prepend>#</template></el-input></el-form-item>
      <el-form-item label="下游研究数据">
        <el-checkbox :model-value="dashboard.routeIngestLocked ? true : dashboard.groupRelayRouteForm.ingest" :disabled="dashboard.routeIngestLocked" @change="(value: unknown) => { dashboard.groupRelayRouteForm.ingest = Boolean(value); }">这个标签的消息进入下游研究数据</el-checkbox>
        <el-text v-if="dashboard.routeIngestHint" size="small" type="info" style="display: block; width: 100%">{{ dashboard.routeIngestHint }}</el-text>
      </el-form-item>
      <el-form-item label="转发到的目标群">
        <el-select v-model="dashboard.groupRelayRouteForm.target_chat_ids" multiple filterable allow-create default-first-option placeholder="选择目标群（可多选）" style="width: 100%">
          <el-option v-for="target in dashboard.relayTargetOptions" :key="target.value" :value="target.value" :label="target.label" />
        </el-select>
        <el-text size="small" type="info">列表里是已配置好的目标群。要转发到一个新的群：<el-button link type="primary" @click="dashboard.openTargetDialog()">新增目标群</el-button>（选群即可，机器人自动创建）</el-text>
      </el-form-item>
      <el-collapse class="section-gap"><el-collapse-item title="高级（一般不用填）">
        <el-form-item label="源群官方 chat_id（仅 OAuth 补读用）"><el-input v-model="dashboard.groupRelayRouteForm.chat_id" placeholder="oc_xxx" /></el-form-item>
        <el-form-item label="按群名追加目标群（走 OAuth 解析）"><el-input v-model="dashboard.groupRelayRouteForm.target_chat_names_text" placeholder="例如：anqiang分享群1, 复盘群" /></el-form-item>
      </el-collapse-item></el-collapse>
      <el-form-item label="状态"><el-switch v-model="dashboard.groupRelayRouteForm.enabled" active-text="启用监听" inactive-text="停用监听" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="dashboard.groupRelayRouteDialog = false">取消</el-button><el-button type="primary" :loading="dashboard.groupRelayRouteSaving" @click="dashboard.saveGroupRelayRoute">保存</el-button></template>
  </el-dialog>

  <el-dialog v-model="dashboard.targetDialog" :title="dashboard.targetForm.chat_id ? '重新配置目标群' : '新增目标群'" width="560px" destroy-on-close append-to-body>
    <el-form label-position="top" @submit.prevent="dashboard.saveRelayTarget">
      <el-form-item label="机器人">
        <el-radio-group v-model="dashboard.targetForm.mode">
          <el-radio-button value="auto">自动创建机器人</el-radio-button>
          <el-radio-button value="manual">手动填写 webhook</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-alert v-if="dashboard.targetForm.mode === 'auto'" title="选一个本账号已加入的群即可：系统用本账号在群里添加一个名为“转发机器人”的自定义机器人并设好关键词，群里已有它就直接沿用。保存时会向该群发一条测试消息。" type="info" :closable="false" show-icon class="section-gap" />
      <el-alert v-else title="用于账号不在的群，或想用已有的机器人：在飞书里打开目标群 → 设置 → 群机器人 → 添加机器人 → 自定义机器人，安全设置选“自定义关键词”，再把 webhook 地址和关键词填到这里。保存时会向该群发一条测试消息，发不出去就不保存。" type="info" :closable="false" show-icon class="section-gap" />
      <el-form-item label="目标群" required>
        <el-select :model-value="dashboard.targetForm.websocket_chat_id || dashboard.targetForm.name" filterable allow-create default-first-option :loading="dashboard.joinedGroupsLoading" placeholder="选择已加入的群，或输入群名" style="width: 100%" @change="dashboard.pickTargetGroup">
          <el-option v-for="group in dashboard.pickableGroups" :key="group.chat_id" :value="group.chat_id" :label="group.name || group.chat_id" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="dashboard.targetForm.mode === 'manual'" label="机器人 webhook 地址" required><el-input v-model="dashboard.targetForm.webhook_url" type="password" show-password autocomplete="off" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/…" /></el-form-item>
      <el-form-item v-if="dashboard.targetForm.mode === 'manual'" label="关键词" required><el-input v-model="dashboard.targetForm.keyword" maxlength="40" placeholder="机器人安全设置里填的自定义关键词" /></el-form-item>
      <el-form-item v-else label="关键词（可不填）"><el-input v-model="dashboard.targetForm.keyword" maxlength="40" placeholder="默认：转发。每条转发消息里会带上它，机器人靠它放行" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="dashboard.targetDialog = false">取消</el-button><el-button type="primary" :loading="dashboard.targetSaving" @click="dashboard.saveRelayTarget">{{ dashboard.targetForm.mode === 'auto' ? '创建机器人并保存' : '发测试消息并保存' }}</el-button></template>
  </el-dialog>

  <el-dialog v-model="dashboard.bundleDialog" :title="`导出资料包 · ${dashboard.bundleRoute?.chat_name ?? ''}`" width="560px" destroy-on-close>
    <el-alert title="导出该群在所选日期内已转发的全部消息：transcript.md（按时间排列的正文）、messages.jsonl（结构化数据）和 images/ 目录里的图片文件，打包成 zip，可直接交给 AI 阅读。" type="info" :closable="false" show-icon class="section-gap" />
    <el-form label-position="top">
      <el-form-item label="导出内容">
        <el-radio-group v-model="dashboard.bundleSource" @change="dashboard.previewBundle">
          <el-radio value="full">完整历史（从飞书读取，含未转发的和监听之前的消息）</el-radio>
          <el-radio value="relayed">仅已转发的消息</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="时间范围（北京时间，含首尾两天）">
        <el-date-picker v-model="dashboard.bundleDates" type="daterange" value-format="YYYY-MM-DD" range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期" :clearable="false" class="full-width" @change="dashboard.previewBundle" />
      </el-form-item>
    </el-form>
    <el-alert v-if="dashboard.bundleError" :title="dashboard.bundleError" type="error" :closable="false" show-icon />
    <el-descriptions v-else-if="dashboard.bundlePreview" :column="1" border size="small">
      <el-descriptions-item label="文件名">{{ dashboard.bundlePreview.filename }}</el-descriptions-item>
      <el-descriptions-item v-if="dashboard.bundlePreview.source === 'full' && dashboard.bundlePreview.history_origin === 'relayed_only'" label="群历史">读不到（{{ dashboard.bundlePreview.history_reason }}），也没有保存过，只能导出已转发的消息</el-descriptions-item>
      <el-descriptions-item v-else-if="dashboard.bundlePreview.source === 'full' && dashboard.bundlePreview.history_origin === 'stored'" label="群历史">已保存的副本 {{ dashboard.bundlePreview.history_positions ?? 0 }} 条（{{ dashboard.bundlePreview.history_reason }}）</el-descriptions-item>
      <el-descriptions-item v-else-if="dashboard.bundlePreview.source === 'full'" label="群历史">约 {{ dashboard.bundlePreview.history_positions ?? 0 }} 条（按消息位置估算，实际数量以导出为准）</el-descriptions-item>
      <el-descriptions-item :label="dashboard.bundlePreview.source === 'full' ? '其中已转发' : '消息'">{{ dashboard.bundlePreview.messages }} 条</el-descriptions-item>
      <el-descriptions-item :label="dashboard.bundlePreview.source === 'full' ? '已转发部分的图片' : '图片'">{{ dashboard.bundlePreview.images }} 张</el-descriptions-item>
      <el-descriptions-item v-if="dashboard.bundlePreview.first_message_at" label="实际覆盖">{{ dashboard.dateText(dashboard.bundlePreview.first_message_at) }} 至 {{ dashboard.dateText(dashboard.bundlePreview.last_message_at) }}</el-descriptions-item>
    </el-descriptions>
    <template #footer>
      <el-button @click="dashboard.bundleDialog = false">取消</el-button>
      <el-button type="primary" :loading="dashboard.bundleLoading" :disabled="!dashboard.bundleHasContent" @click="dashboard.downloadBundle">{{ dashboard.bundlePreview && !dashboard.bundleHasContent ? '该范围内没有消息' : '下载 zip' }}</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="dashboard.workbenchIntegrationDialog" :title="dashboard.workbenchIntegration.title" width="620px" destroy-on-close>
    <el-alert title="此操作会真实调用飞书 API，请确认后台权限已经发布。" type="warning" :closable="false" show-icon class="section-gap" />
    <el-form label-position="top"><el-form-item label="请求 JSON"><el-input v-model="dashboard.workbenchIntegration.payloadText" type="textarea" :rows="12" class="mono" /></el-form-item></el-form>
    <template #footer><el-button @click="dashboard.workbenchIntegrationDialog = false">取消</el-button><el-button type="primary" :loading="dashboard.feishuWorkbenchAction.startsWith('integration:')" @click="dashboard.submitWorkbenchIntegration">提交</el-button></template>
  </el-dialog>
</template>
