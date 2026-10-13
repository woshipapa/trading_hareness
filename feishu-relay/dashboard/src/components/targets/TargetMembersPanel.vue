<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { targetGroupsContextKey } from '../../dashboard-context';
import { useTargetMembers } from '../../composables/useTargetMembers';
import type { TargetMemberApplication } from '../../api/target-members';

const selection = inject(targetGroupsContextKey);
if (!selection) throw new Error('target member management requires the target groups context');

const state = useTargetMembers();
const activeTab = ref('members');
const openedTabs = new Set<string>(['members']);
const selectedTarget = computed(() => selection.selectedTarget.value);
const targetChatId = computed(() => selectedTarget.value?.chat_id ?? '');
const canManage = computed(() => Boolean(selectedTarget.value && selectedTarget.value.owned === true && /^\d+$/.test(String(selectedTarget.value.websocket_chat_id ?? ''))));
const targetLabel = computed(() => selectedTarget.value?.name || selectedTarget.value?.chat_id || '目标群');

function formatTime(value: number | null | undefined): string {
  if (value === null || value === undefined) return '-';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(Number(value) * 1000));
}

async function loadTab(tab: string) {
  if (!canManage.value) return;
  const chatId = targetChatId.value;
  if (tab === 'members') await state.loadMembers(chatId);
  else if (tab === 'history') await state.loadHistory(chatId);
  else await state.loadApplications(chatId);
}

function onTabChange(tab: string) {
  openedTabs.add(tab);
  void loadTab(tab);
}

async function removeMember(row: { user_id: string; name: string }) {
  try {
    await ElMessageBox.confirm(`确认移除成员「${row.name || row.user_id}」？`, '移除成员', { type: 'warning', confirmButtonText: '移除', cancelButtonText: '取消' });
  } catch {
    return;
  }
  if (await state.removeMember(targetChatId.value, row.user_id)) {
    ElMessage.success('成员已移除');
    await state.loadMembers(targetChatId.value);
  }
}

function contactName(application: TargetMemberApplication): string {
  const summary = application.contact_summary;
  if (summary) {
    const name = summary.name ?? summary.display_name ?? summary.displayName;
    if (name) return String(name);
    const id = summary.id ?? summary.user_id ?? summary.userId;
    if (id) return String(id);
  }
  return String(application.id);
}

function statusText(status: number): string {
  return ({ 1: '待处理', 2: '已通过', 3: '已拒绝', 4: '已删除', 5: '已过期' } as Record<number, string>)[status] ?? `状态 ${status}`;
}

async function decideApplication(application: TargetMemberApplication, agree: boolean) {
  if (!agree) {
    try {
      await ElMessageBox.confirm(`确认拒绝「${contactName(application)}」的入群申请？`, '拒绝入群申请', { type: 'warning', confirmButtonText: '拒绝', cancelButtonText: '取消' });
    } catch {
      return;
    }
  }
  if (await state.decideApplication(targetChatId.value, application.id, agree)) {
    ElMessage.success(agree ? '已通过入群申请' : '已拒绝入群申请');
    await state.loadApplications(targetChatId.value);
  }
}

async function loadMoreHistory() {
  const cursor = state.nextHistoryCursor.value;
  if (cursor !== null) await state.loadHistory(targetChatId.value, cursor, true);
}

watch(() => selectedTarget.value?.chat_id, () => {
  state.reset();
  if (canManage.value) for (const tab of openedTabs) void loadTab(tab);
}, { immediate: true });

onBeforeUnmount(() => openedTabs.clear());
</script>

<template>
  <el-card shadow="never" class="section-gap">
    <template #header><div class="card-header"><span>成员管理</span><span v-if="selectedTarget">{{ targetLabel }}</span></div></template>
    <el-text v-if="!selectedTarget" type="info">在上面的表里选一个目标群</el-text>
    <el-text v-else-if="!canManage" type="info">成员管理需要一个本账号是群主且已识别数字群 ID 的目标群；如果群是刚添加的，请先点击“识别现有目标群”。</el-text>
    <template v-else>
      <el-alert v-if="state.error" :title="state.error" type="error" :closable="false" show-icon class="section-gap" />
      <el-tabs v-model="activeTab" @tab-change="onTabChange">
        <el-tab-pane label="成员" name="members">
          <el-table :data="state.members" :loading="state.loading.members" row-key="user_id">
            <el-table-column label="成员" min-width="180"><template #default="{ row }">{{ row.name || row.user_id }}</template></el-table-column>
            <el-table-column label="入群时间" min-width="180"><template #default="{ row }">{{ formatTime(row.joined_at) }}</template></el-table-column>
            <el-table-column label="操作" width="100"><template #default="{ row }"><el-button type="danger" link :loading="state.loading.action" @click="removeMember(row)">移除</el-button></template></el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="进退群记录" name="history">
          <el-table :data="state.history" :loading="state.loading.history">
            <el-table-column label="成员" min-width="180"><template #default="{ row }">{{ row.name || row.user_id }}</template></el-table-column>
            <el-table-column label="动作" width="100"><template #default="{ row }">{{ row.action === 'join' ? '进群' : '退群' }}</template></el-table-column>
            <el-table-column label="方式" width="100"><template #default="{ row }"><el-tag size="small">{{ row.event }}</el-tag></template></el-table-column>
            <el-table-column label="时间" min-width="180"><template #default="{ row }">{{ formatTime(row.at) }}</template></el-table-column>
          </el-table>
          <div v-if="state.nextHistoryCursor !== null" class="section-gap"><el-button :loading="state.loading.history" @click="loadMoreHistory">加载更多</el-button></div>
        </el-tab-pane>
        <el-tab-pane label="入群申请" name="applications">
          <el-table :data="state.applications" :loading="state.loading.applications" row-key="id">
            <el-table-column label="申请人" min-width="180"><template #default="{ row }">{{ contactName(row) }}</template></el-table-column>
            <el-table-column label="申请时间" min-width="180"><template #default="{ row }">{{ formatTime(row.apply_time) }}</template></el-table-column>
            <el-table-column label="留言" min-width="220"><template #default="{ row }">{{ row.message || '-' }}</template></el-table-column>
            <el-table-column label="状态" width="110"><template #default="{ row }">{{ statusText(row.status) }}</template></el-table-column>
            <el-table-column label="操作" width="160"><template #default="{ row }"><template v-if="row.status === 1"><el-button type="primary" link :loading="state.loading.action" @click="decideApplication(row, true)">通过</el-button><el-button type="danger" link :loading="state.loading.action" @click="decideApplication(row, false)">拒绝</el-button></template><span v-else>-</span></template></el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </template>
  </el-card>
</template>
