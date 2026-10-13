<script setup lang="ts">
import { inject, onMounted } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { targetGroupsContextKey } from '../../dashboard-context';
import { useTargetRegistry } from '../../composables/useTargetRegistry';
import type { TargetRegistryRow } from '../../api/target-registry';

const selection = inject(targetGroupsContextKey);
if (!selection) throw new Error('target registry requires the target groups context');

const registry = useTargetRegistry();
const selectTarget = (target: TargetRegistryRow) => { selection.selectedTarget.value = target; };
const rowClassName = ({ row }: { row: TargetRegistryRow }) => row.chat_id === selection.selectedTarget.value?.chat_id ? 'target-row-selected' : '';
const originText = (origin: TargetRegistryRow['origin']) => origin === 'env' ? '转发目标（环境变量）' : origin === 'dashboard' ? '转发目标（看板）' : '仅管理';
const ownerText = (owned: TargetRegistryRow['owned']) => owned === true ? '是' : owned === false ? '否' : '未识别';
const botText = (target: TargetRegistryRow) => target.bot_name || target.bot_id || '未识别';

const connect = async (row: TargetRegistryRow) => {
  let keyword = '';
  try {
    const answer = await ElMessageBox.prompt(
      '群里会多一个名叫“转发机器人”的机器人，并出现一条测试消息，群成员都能看到。到期提醒由它发出；关键词会出现在它每条消息的开头。',
      `给“${row.name || row.chat_id}”建机器人`,
      { inputValue: '提醒', inputPattern: /^\S.{0,39}$/, inputErrorMessage: '关键词为 1–40 个字', confirmButtonText: '建机器人', cancelButtonText: '取消' },
    );
    keyword = String(answer.value ?? '').trim();
  } catch {
    return;
  }
  const created = await registry.connect(row, keyword);
  if (!created) return;
  selection.selectedTarget.value = created;
  ElMessage.success('机器人已建好：这个群可以发到期提醒，也可以选作转发目标');
};

onMounted(() => { void registry.load(); });
</script>

<template>
  <el-card shadow="never" class="section-gap">
    <template #header>
      <div class="card-header"><span>我的群（转发目标，以及我是群主的群）</span><el-button type="primary" :loading="registry.resolving.value" @click="registry.resolve">刷新群列表</el-button></div>
    </template>
    <el-alert v-if="registry.error.value" :title="registry.error.value" type="error" :closable="false" show-icon class="section-gap" />
    <el-table v-loading="registry.loading.value" :data="registry.targets.value" row-key="chat_id" :row-class-name="rowClassName" @row-click="selectTarget">
      <el-table-column prop="name" label="群名" min-width="190"><template #default="{ row }">{{ row.name || row.chat_id }}</template></el-table-column>
      <el-table-column label="类型" width="170"><template #default="{ row }">{{ originText(row.origin) }}</template></el-table-column>
      <el-table-column prop="keyword" label="关键词" min-width="110"><template #default="{ row }">{{ row.keyword || '-' }}</template></el-table-column>
      <el-table-column label="机器人" min-width="200">
        <template #default="{ row }">
          <template v-if="row.origin === 'owned'">
            <span class="registry-no-bot">未接入</span>
            <el-button size="small" :loading="registry.connecting.value === row.chat_id" :disabled="Boolean(registry.connecting.value)" @click.stop="connect(row)">建机器人</el-button>
          </template>
          <template v-else>{{ botText(row) }}</template>
        </template>
      </el-table-column>
      <el-table-column label="是否群主" width="100"><template #default="{ row }">{{ ownerText(row.owned) }}</template></el-table-column>
    </el-table>
    <div class="registry-hint">选中一行后，下面可以管理它的成员和会员期限。“仅管理”的群没有机器人：成员和期限照常能管，发到期提醒前要先建机器人。</div>
  </el-card>
</template>

<style scoped>
.registry-no-bot { color: var(--el-text-color-secondary); margin-right: 10px; }
.registry-hint { color: var(--el-text-color-secondary); font-size: 13px; margin-top: 10px; }
</style>
