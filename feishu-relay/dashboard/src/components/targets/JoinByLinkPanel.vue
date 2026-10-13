<script setup lang="ts">
import { useJoinByLink } from '../../composables/useJoinByLink';

const { expanded, link, info, loading, joining, error, expand, collapse, preview, join, choose } = useJoinByLink();

const emit = defineEmits<{ joined: [chatId: string] }>();

function formatExpireTime(value: number) {
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value * 1000));
}

async function joinGroup() {
  const result = await join();
  if (result) emit('joined', result.chat_id);
}

function chooseGroup() {
  const chatId = choose();
  if (chatId) emit('joined', chatId);
}
</script>

<template>
  <div class="join-by-link">
    <el-button v-if="!expanded" link type="primary" class="join-by-link-prompt" @click="expand">
      还没加入这个群？粘贴邀请链接
    </el-button>
    <template v-else>
      <div class="join-by-link-input">
        <el-input v-model="link" placeholder="粘贴飞书群邀请链接" autocomplete="off" @keyup.enter="preview">
          <template #prepend>邀请链接</template>
        </el-input>
        <el-button type="primary" :loading="loading" :disabled="!link.trim()" @click="preview">查看</el-button>
        <el-button link @click="collapse">收起</el-button>
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="join-by-link-gap" />
      <el-descriptions v-if="info" :column="2" border size="small" class="join-by-link-gap">
        <el-descriptions-item label="群名">{{ info.name }}</el-descriptions-item>
        <el-descriptions-item label="人数">{{ info.member_count }} 人</el-descriptions-item>
        <el-descriptions-item label="链接到期">{{ formatExpireTime(info.expire_time) }}</el-descriptions-item>
        <el-descriptions-item label="入群状态">
          <el-tag :type="info.already_in_chat ? 'success' : 'warning'" size="small">
            {{ info.already_in_chat ? '已在群内' : '尚未加入' }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>
      <div v-if="info" class="join-by-link-actions">
        <el-button v-if="info.already_in_chat" type="primary" size="small" @click="chooseGroup">选这个群</el-button>
        <el-button v-else type="primary" size="small" :loading="joining" @click="joinGroup">加入这个群</el-button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.join-by-link { margin: 0 0 14px; }
.join-by-link-prompt { padding: 0; }
.join-by-link-input { display: flex; align-items: center; gap: 8px; }
.join-by-link-input .el-input { min-width: 0; flex: 1; }
.join-by-link-gap { margin-top: 10px; }
.join-by-link-actions { display: flex; justify-content: flex-end; margin-top: 10px; }
@media (max-width: 520px) {
  .join-by-link-input { align-items: stretch; flex-wrap: wrap; }
  .join-by-link-input .el-input { flex-basis: 100%; }
}
</style>
