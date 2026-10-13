<script setup lang="ts">
import { computed, inject } from 'vue';
import { CopyDocument, Refresh } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { targetGroupsContextKey } from '../../dashboard-context';
import { useTargetGroupCreate } from '../../composables/useTargetGroupCreate';

const selection = inject(targetGroupsContextKey);
if (!selection) throw new Error('target group creation requires the target groups context');

const group = useTargetGroupCreate();
const selectedTarget = selection.selectedTarget;
const canRegenerateInvite = computed(() => {
  const target = selectedTarget.value;
  return target?.owned === true && /^\d+$/.test(String(target.websocket_chat_id ?? ''));
});

async function copy(value: string, label: string) {
  try {
    await navigator.clipboard.writeText(value);
    ElMessage.success(`${label}已复制`);
  } catch {
    ElMessage.warning('复制失败，请手动选中复制');
  }
}

function regenerateInvite() {
  const target = selectedTarget.value;
  if (!target || !canRegenerateInvite.value) return;
  void group.regenerateInvite(target.chat_id, target.name ?? target.chat_id);
}
</script>

<template>
  <el-card shadow="never" class="section-gap">
    <template #header>
      <div class="card-header"><span>新建同步群</span><small>创建群、机器人并生成邀请链接</small></div>
    </template>
    <el-form label-width="92px" @submit.prevent="group.createGroup">
      <el-form-item label="群名" required>
        <el-input v-model="group.name.value" maxlength="60" show-word-limit placeholder="请输入同步群名称" />
      </el-form-item>
      <el-form-item label="关键词">
        <el-input v-model="group.keyword.value" maxlength="40" placeholder="默认：转发" />
      </el-form-item>
      <el-form-item>
        <el-checkbox v-model="group.onlyOwnerPosts.value">只许群主发言</el-checkbox>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="group.submitting.value" @click="group.createGroup">创建</el-button>
      </el-form-item>
    </el-form>

    <el-alert v-if="group.error.value" :title="group.error.value" type="error" :closable="false" show-icon class="section-gap" />

    <div v-if="group.invite.value" class="invite-card">
      <div class="invite-header">{{ group.inviteTargetName.value }}的邀请信息</div>
      <el-form label-width="92px">
        <el-form-item label="邀请链接">
          <el-input :model-value="group.invite.value.shared_url" readonly>
            <template #append><el-button :icon="CopyDocument" @click="copy(group.invite.value!.shared_url, '邀请链接')">复制</el-button></template>
          </el-input>
        </el-form-item>
        <el-form-item label="口令">
          <el-input :model-value="group.invite.value.kou_ling" readonly>
            <template #append><el-button :icon="CopyDocument" @click="copy(group.invite.value!.kou_ling, '口令')">复制</el-button></template>
          </el-input>
        </el-form-item>
      </el-form>
      <el-alert title="群已创建，现在可以在“新增源群”里选择它作为目标群。" type="success" :closable="false" show-icon />
    </div>

    <el-button v-if="canRegenerateInvite" class="regenerate-button" :loading="group.submitting.value" :icon="Refresh" @click="regenerateInvite">
      重新生成邀请链接
    </el-button>
  </el-card>
</template>

<style scoped>
.invite-card { margin-top: 18px; padding: 14px; border: 1px solid var(--el-border-color-light); background: #f8fafc; }
.invite-header { margin-bottom: 14px; font-weight: 600; }
.regenerate-button { margin-top: 14px; }
</style>
