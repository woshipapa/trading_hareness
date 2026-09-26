<script setup lang="ts">
import { DataAnalysis, Document, Operation, Refresh, UploadFilled } from '@element-plus/icons-vue';
import GroupRelayMonitorView from './views/GroupRelayMonitorView.vue';
import FeishuWorkbenchView from './views/FeishuWorkbenchView.vue';
import ManualRelayView from './views/ManualRelayView.vue';
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
      </el-menu>
      <div class="side-state"><el-tag :type="dashboard.connected ? 'success' : 'warning'" effect="plain">{{ dashboard.connected ? '事件流已连接' : '事件流重连中' }}</el-tag></div>
    </el-aside>
    <el-container>
      <el-header class="topbar"><div><h1>{{ dashboard.activeSection === 'monitor' ? '群监听状态' : dashboard.activeSection === 'workbench' ? '飞书工作台' : '手动投递' }}</h1><span>{{ dashboard.activeSection === 'monitor' ? 'LarkAgentX WebSocket、webhook 和 Itougu 状态' : dashboard.activeSection === 'workbench' ? '汇总群协作闭环、能力与授权状态' : '通过适配器提交人工消息和媒体' }}</span></div><el-button :icon="Refresh" :loading="dashboard.activeSection === 'monitor' ? dashboard.groupRelayLoading : dashboard.feishuWorkbenchLoading" @click="dashboard.activeSection === 'monitor' ? dashboard.loadGroupRelayStatus() : dashboard.loadFeishuWorkbench()">刷新数据</el-button></el-header>
      <el-main class="content">
        <GroupRelayMonitorView v-if="dashboard.activeSection === 'monitor'" />
        <FeishuWorkbenchView v-else-if="dashboard.activeSection === 'workbench'" />
        <ManualRelayView v-else />
      </el-main>
    </el-container>
  </el-container>

  <el-dialog v-model="dashboard.groupRelayRouteDialog" :title="dashboard.groupRelayRouteForm.key ? '编辑源群' : '新增源群'" width="520px" destroy-on-close>
    <el-form label-position="top" @submit.prevent="dashboard.saveGroupRelayRoute">
      <el-alert title="保存时会用用户读取权限搜索群名；新增源群会等待下一条消息自动绑定 LarkAgentX WebSocket。" type="info" :closable="false" show-icon class="section-gap" />
      <el-form-item label="源群名称" required><el-input v-model="dashboard.groupRelayRouteForm.chat_name" maxlength="120" placeholder="例如：新野人哥会员群【禁言】" /></el-form-item>
      <el-form-item label="转发标签" required><el-input v-model="dashboard.groupRelayRouteForm.tag" maxlength="32" placeholder="例如：quanneng"><template #prepend>#</template></el-input></el-form-item>
      <el-form-item label="群 chat_id（可选）"><el-input v-model="dashboard.groupRelayRouteForm.chat_id" placeholder="oc_xxx" /></el-form-item>
      <el-form-item label="额外转发目标群名"><el-input v-model="dashboard.groupRelayRouteForm.target_chat_names_text" placeholder="例如：anqiang分享群1, 复盘群" /></el-form-item>
      <el-form-item label="已知目标群 chat_id"><el-input v-model="dashboard.groupRelayRouteForm.target_chat_ids_text" placeholder="oc_xxx, oc_yyy" /></el-form-item>
      <el-form-item label="状态"><el-switch v-model="dashboard.groupRelayRouteForm.enabled" active-text="启用监听" inactive-text="停用监听" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="dashboard.groupRelayRouteDialog = false">取消</el-button><el-button type="primary" :loading="dashboard.groupRelayRouteSaving" @click="dashboard.saveGroupRelayRoute">保存</el-button></template>
  </el-dialog>

  <el-dialog v-model="dashboard.workbenchIntegrationDialog" :title="dashboard.workbenchIntegration.title" width="620px" destroy-on-close>
    <el-alert title="此操作会真实调用飞书 API，请确认后台权限已经发布。" type="warning" :closable="false" show-icon class="section-gap" />
    <el-form label-position="top"><el-form-item label="请求 JSON"><el-input v-model="dashboard.workbenchIntegration.payloadText" type="textarea" :rows="12" class="mono" /></el-form-item></el-form>
    <template #footer><el-button @click="dashboard.workbenchIntegrationDialog = false">取消</el-button><el-button type="primary" :loading="dashboard.feishuWorkbenchAction.startsWith('integration:')" @click="dashboard.submitWorkbenchIntegration">提交</el-button></template>
  </el-dialog>
</template>
