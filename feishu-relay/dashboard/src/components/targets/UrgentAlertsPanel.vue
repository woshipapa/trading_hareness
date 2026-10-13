<script setup lang="ts">
import { onMounted } from 'vue';
import { useUrgentAlerts } from '../../composables/useUrgentAlerts';

const { settings, recipients, loading, saving, testing, error, dirty, hasRecipient, selectedRecipient, load, save, sendTest } = useUrgentAlerts();

onMounted(() => { void load(); });
</script>

<template>
  <el-card shadow="never" class="section-gap">
    <template #header>
      <div class="card-header"><span>告警加急</span><el-tag :type="settings.enabled ? 'success' : 'info'">{{ settings.enabled ? '已开启' : '未开启' }}</el-tag></div>
    </template>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="section-gap" />
    <p class="group-relay-age">中继降级类告警除发到运维群外，再由本账号发给接收人并应用内加急；每小时最多 6 次；账号登录失效时发不了加急。</p>
    <el-text v-if="!loading && !recipients.length" type="info">加急接收人必须是本账号与接收人的两人会话，当前没有找到符合条件的会话。</el-text>
    <el-form v-else label-position="top" :disabled="loading" class="urgent-alert-form">
      <el-form-item label="接收人">
        <el-select v-model="selectedRecipient" placeholder="选择接收人" class="full-width">
          <el-option v-for="recipient in recipients" :key="`${recipient.chat_id}:${recipient.user_id}`" :label="recipient.name || recipient.chat_id" :value="`${recipient.chat_id}:${recipient.user_id}`" />
        </el-select>
      </el-form-item>
      <el-form-item label="开关">
        <el-switch v-model="settings.enabled" active-text="开启加急" inactive-text="关闭加急" />
      </el-form-item>
      <el-space>
        <el-button type="primary" :loading="saving" :disabled="!dirty" @click="save">保存</el-button>
        <el-button :loading="testing" :disabled="dirty || !hasRecipient" @click="sendTest">发一条测试加急</el-button>
      </el-space>
    </el-form>
  </el-card>
</template>
