<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { groupRelayApi, type SendAuditRow, type SendStats } from '../api/group-relay';

const chatId = ref('');
const text = ref('');
const rootId = ref('');
const sending = ref(false);
const message = ref<{ type: 'success' | 'warning' | 'error'; text: string } | null>(null);
const audit = ref<SendAuditRow[]>([]);
const stats = ref<SendStats | null>(null);
// One key per composed message: a double-click or retry replays the first
// result instead of sending twice. It rotates only after a settled send.
const idempotencyKey = ref(crypto.randomUUID());

const chats = computed(() => Object.entries(stats.value?.chat_remaining ?? {}));

async function load() {
  try {
    const result = await groupRelayApi.sendAudit(50);
    audit.value = result.audit;
    stats.value = result.stats;
    if (!chatId.value && chats.value.length) chatId.value = chats.value[0][0];
  } catch (error) {
    message.value = { type: 'error', text: error instanceof Error ? error.message : String(error) };
  }
}

async function send() {
  sending.value = true;
  message.value = null;
  try {
    const result = await groupRelayApi.sendMessage<{ status: string; idempotent_replay?: boolean }>({
      chat_id: chatId.value, text: text.value, root_id: rootId.value.trim() || undefined, idempotency_key: idempotencyKey.value,
    });
    message.value = { type: 'success', text: result.idempotent_replay ? '相同请求已发送过,未重复发送' : '已发送' };
    text.value = '';
    rootId.value = '';
    idempotencyKey.value = crypto.randomUUID();
  } catch (error) {
    const raw = error instanceof Error ? error.message : String(error);
    message.value = { type: /超限|rate/i.test(raw) ? 'warning' : 'error', text: raw };
  } finally {
    sending.value = false;
    void load();
  }
}

const when = (seconds: number) => new Date(seconds * 1000).toLocaleString();
const resultType = (result: string) => (result === 'accepted' ? 'success' : result === 'failed' ? 'danger' : 'warning');

onMounted(load);
</script>

<template>
  <el-card shadow="never" header="发送">
    <el-alert type="warning" :closable="false" show-icon class="section-gap" title="以个人账号发送,仅限白名单群;超限会被拒绝并给出等待时间。" />
    <el-form label-position="top">
      <el-form-item label="目标群(白名单)">
        <el-select v-model="chatId" class="full-width">
          <el-option v-for="[id, remaining] in chats" :key="id" :value="id" :label="`${id} · 本分钟剩余 ${remaining}`" />
        </el-select>
      </el-form-item>
      <el-form-item label="回复 root_id(可选)"><el-input v-model="rootId" maxlength="128" /></el-form-item>
      <el-form-item label="正文"><el-input v-model="text" type="textarea" :rows="6" maxlength="10000" show-word-limit /></el-form-item>
      <el-alert v-if="message" :title="message.text" :type="message.type" :closable="false" class="section-gap" />
      <el-button type="primary" :loading="sending" :disabled="!chatId || !text.trim()" @click="send">发送</el-button>
      <span v-if="stats" class="group-relay-age"> 全局本小时剩余 {{ stats.global_remaining }} / {{ stats.global_per_hour }}</span>
    </el-form>
  </el-card>
  <el-card shadow="never" header="审计" class="section-gap">
    <el-table :data="audit" size="small" empty-text="暂无发送记录">
      <el-table-column label="时间" width="170"><template #default="{ row }">{{ when(row.created_at) }}</template></el-table-column>
      <el-table-column prop="chat_id" label="群" min-width="150" />
      <el-table-column label="结果" width="90"><template #default="{ row }"><el-tag :type="resultType(row.result)" size="small">{{ row.result }}</el-tag></template></el-table-column>
      <el-table-column prop="text_length" label="字数" width="70" />
      <el-table-column prop="error" label="错误" min-width="160" />
    </el-table>
  </el-card>
</template>
