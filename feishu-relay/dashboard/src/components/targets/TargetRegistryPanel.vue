<script setup lang="ts">
import { inject, onMounted, reactive } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { targetGroupsContextKey } from '../../dashboard-context';
import { useTargetRegistry } from '../../composables/useTargetRegistry';
import type { ExistingBot, TargetRegistryRow } from '../../api/target-registry';

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

const botDialog = reactive({ visible: false, loading: false, using: '', row: null as TargetRegistryRow | null, bots: [] as ExistingBot[] });

const openBots = async (row: TargetRegistryRow) => {
  Object.assign(botDialog, { visible: true, loading: true, using: '', row, bots: [] });
  botDialog.bots = await registry.existingBots(row);
  botDialog.loading = false;
};

const useBot = async (bot: ExistingBot) => {
  if (!botDialog.row) return;
  botDialog.using = bot.bot_id;
  const connected = await registry.useBot(botDialog.row, bot.bot_id);
  botDialog.using = '';
  if (!connected) return;
  botDialog.visible = false;
  selection.selectedTarget.value = connected;
  ElMessage.success(`已接入“${bot.name}”：没有往群里发消息，机器人的设置也没有改`);
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
            <el-button size="small" type="primary" plain :disabled="Boolean(registry.connecting.value)" @click.stop="openBots(row)">用已有机器人</el-button>
            <el-button size="small" :loading="registry.connecting.value === row.chat_id && !botDialog.visible" :disabled="Boolean(registry.connecting.value)" @click.stop="connect(row)">建机器人</el-button>
          </template>
          <template v-else>{{ botText(row) }}</template>
        </template>
      </el-table-column>
      <el-table-column label="是否群主" width="100"><template #default="{ row }">{{ ownerText(row.owned) }}</template></el-table-column>
    </el-table>
    <div class="registry-hint">选中一行后，下面可以管理它的成员和会员期限。“仅管理”的群还没接入机器人：成员和期限照常能管，发到期提醒前要先接入——群里已经有机器人就点“用已有机器人”，没有再“建机器人”。</div>
    <el-dialog v-model="botDialog.visible" :title="`“${botDialog.row?.name || ''}”里已有的机器人`" width="620px">
      <el-alert v-if="registry.error.value" :title="registry.error.value" type="error" :closable="false" show-icon class="section-gap" />
      <el-table v-loading="botDialog.loading" :data="botDialog.bots" row-key="bot_id" empty-text="这个群里没有自定义机器人，可以关掉这个窗口点“建机器人”">
        <el-table-column prop="name" label="机器人" min-width="150" />
        <el-table-column label="关键词" min-width="120"><template #default="{ row }">{{ row.key_words.length ? row.key_words.join('、') : '无' }}</template></el-table-column>
        <el-table-column label="" min-width="210">
          <template #default="{ row }">
            <el-button v-if="row.usable" size="small" type="primary" :loading="botDialog.using === row.bot_id" :disabled="Boolean(botDialog.using)" @click="useBot(row)">用它发提醒</el-button>
            <span v-else class="registry-no-bot">{{ row.reason }}</span>
          </template>
        </el-table-column>
      </el-table>
      <div class="registry-hint">接入时不会往群里发消息，也不会改动机器人；原来用它发消息的程序不受影响。有关键词的机器人，提醒开头会带上它的第一个关键词。</div>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.registry-no-bot { color: var(--el-text-color-secondary); margin-right: 10px; }
.registry-hint { color: var(--el-text-color-secondary); font-size: 13px; margin-top: 10px; }
</style>
