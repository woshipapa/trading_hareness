<script setup lang="ts">
import { inject, onMounted } from 'vue';
import { targetGroupsContextKey } from '../../dashboard-context';
import { useTargetRegistry } from '../../composables/useTargetRegistry';
import type { TargetRegistryRow } from '../../api/target-registry';

const selection = inject(targetGroupsContextKey);
if (!selection) throw new Error('target registry requires the target groups context');

const registry = useTargetRegistry();
const selectTarget = (target: TargetRegistryRow) => { selection.selectedTarget.value = target; };
const rowClassName = ({ row }: { row: TargetRegistryRow }) => row.chat_id === selection.selectedTarget.value?.chat_id ? 'target-row-selected' : '';
const originText = (origin: TargetRegistryRow['origin']) => origin === 'env' ? '环境变量' : '看板';
const ownerText = (owned: TargetRegistryRow['owned']) => owned === true ? '是' : owned === false ? '否' : '未识别';
const botText = (target: TargetRegistryRow) => target.bot_name || target.bot_id || '未识别';

onMounted(() => { void registry.load(); });
</script>

<template>
  <el-card shadow="never" class="section-gap">
    <template #header>
      <div class="card-header"><span>目标群登记</span><el-button type="primary" :loading="registry.resolving" @click="registry.resolve">识别现有目标群</el-button></div>
    </template>
    <el-alert v-if="registry.error" :title="registry.error" type="error" :closable="false" show-icon class="section-gap" />
    <el-table :data="registry.targets" :loading="registry.loading" row-key="chat_id" :row-class-name="rowClassName" @row-click="selectTarget">
      <el-table-column prop="name" label="群名" min-width="190"><template #default="{ row }">{{ row.name || row.chat_id }}</template></el-table-column>
      <el-table-column prop="keyword" label="关键词" min-width="140"><template #default="{ row }">{{ row.keyword || '-' }}</template></el-table-column>
      <el-table-column label="来源" width="110"><template #default="{ row }">{{ originText(row.origin) }}</template></el-table-column>
      <el-table-column label="机器人" min-width="180"><template #default="{ row }">{{ botText(row) }}</template></el-table-column>
      <el-table-column label="是否群主" width="110"><template #default="{ row }">{{ ownerText(row.owned) }}</template></el-table-column>
    </el-table>
  </el-card>
</template>
