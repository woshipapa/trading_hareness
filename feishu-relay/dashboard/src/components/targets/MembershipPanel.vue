<script setup lang="ts">
import { computed, inject, reactive, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Bell, Edit, Refresh, Search, Setting } from '@element-plus/icons-vue';
import { targetGroupsContextKey } from '../../dashboard-context';
import { useMemberships } from '../../composables/useMemberships';
import type { MembershipRow } from '../../api/memberships';

const selection = inject(targetGroupsContextKey);
if (!selection) throw new Error('membership panel requires the target groups context');

const memberships = useMemberships(selection.selectedTarget);
const action = memberships.action;
const quickTerms = [1, 3, 6, 12];
const settingsForm = reactive({ enabled: false, default_term_months: 1, remind_text: '' });
const editDialog = ref(false);
const editError = ref('');
const editing = ref<MembershipRow | null>(null);
const editForm = reactive({ started_on: '', term_months: 1, note: '', exempt: false });

const ready = computed(() => Boolean(memberships.target.value) && memberships.eligible.value);

function copySettings() {
  if (!memberships.settings.value) return;
  Object.assign(settingsForm, {
    enabled: memberships.settings.value.enabled,
    default_term_months: memberships.settings.value.default_term_months,
    remind_text: memberships.settings.value.remind_text,
  });
}

watch(() => memberships.settings.value, copySettings, { immediate: true });

function statusText(row: MembershipRow) {
  return memberships.settings.value?.exempt_user_ids.includes(row.user_id) || row.status === '已豁免' ? '不提醒' : row.status;
}

function statusType(row: MembershipRow) {
  const status = statusText(row);
  if (status === '已到期') return 'danger';
  if (status === '7 天内到期') return 'warning';
  if (status === '正常') return 'success';
  return 'info';
}

function canRemind(row: MembershipRow) {
  return statusText(row) === '已到期' && !row.left_at;
}

async function saveSettings() {
  const saved = await memberships.saveSettings({ ...settingsForm });
  if (saved) ElMessage.success('会员提醒设置已保存');
  else copySettings();
}

async function syncMembers() {
  if (await memberships.sync()) ElMessage.success('成员已同步');
}

async function preview() {
  const rows = await memberships.preview();
  if (!rows) return;
  await ElMessageBox.alert(rows.length ? rows.map((row) => row.display_name).join('、') : '今天没有要提醒的会员', '今天的到期提醒预览', { confirmButtonText: '知道了' });
}

async function review() {
  try {
    await ElMessageBox.confirm('机器人会立即在群里 @ 已到期会员，确认现在发送提醒吗？', '确认立即审查', { type: 'warning', confirmButtonText: '立即审查', cancelButtonText: '取消' });
  } catch {
    return;
  }
  if (await memberships.review()) ElMessage.success('已完成今天的会员审查');
}

function openEdit(row: MembershipRow) {
  editing.value = row;
  editError.value = '';
  Object.assign(editForm, {
    started_on: row.started_on,
    term_months: row.term_months,
    note: row.note ?? '',
    exempt: memberships.settings.value?.exempt_user_ids.includes(row.user_id) ?? false,
  });
  editDialog.value = true;
}

async function saveEdit() {
  if (!editing.value) return;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(editForm.started_on) || !Number.isInteger(editForm.term_months) || editForm.term_months < 1 || editForm.term_months > 120) {
    editError.value = '请填写有效的开始日期和 1–120 的整数期限';
    return;
  }
  editError.value = '';
  if (await memberships.saveMember(editing.value.user_id, { ...editForm, note: editForm.note || null })) {
    editDialog.value = false;
    ElMessage.success('会员信息已保存');
  }
}

async function remind(row: MembershipRow) {
  try {
    await ElMessageBox.confirm(`将由机器人在群里提醒 ${row.display_name}，确认再次提醒吗？`, '确认再次提醒', { type: 'warning', confirmButtonText: '再次提醒', cancelButtonText: '取消' });
  } catch {
    return;
  }
  if (await memberships.remind(row.user_id)) ElMessage.success('已发送再次提醒');
}
</script>

<template>
  <el-card shadow="never" class="section-gap membership-panel">
    <template #header>
      <div class="card-header">
        <span>会员期限与到期提醒</span>
        <el-space v-if="ready" wrap>
          <el-button size="small" :icon="Refresh" :loading="action === 'sync'" @click="syncMembers">同步成员</el-button>
          <el-button size="small" :icon="Search" :loading="action === 'preview'" @click="preview">预览今天要提醒谁</el-button>
          <el-button size="small" type="primary" :icon="Bell" :loading="action === 'review'" @click="review">立即审查</el-button>
        </el-space>
      </div>
    </template>

    <div v-if="!memberships.target.value" class="membership-empty">在上面的表里选一个目标群</div>
    <div v-else-if="!memberships.eligible.value" class="membership-empty">会员期限管理需要本账号是群主，且目标群已有数字群 ID</div>
    <template v-else>
      <el-alert v-if="memberships.error" :title="memberships.error" type="error" :closable="false" show-icon class="section-gap" />
      <el-form :model="settingsForm" label-position="top" class="membership-settings section-gap">
        <el-form-item label="每天自动审查">
          <el-switch v-model="settingsForm.enabled" active-text="开启" inactive-text="关闭" />
        </el-form-item>
        <el-form-item label="默认期限">
          <el-space wrap>
            <el-radio-group v-model="settingsForm.default_term_months" size="small">
              <el-radio-button v-for="term in quickTerms" :key="term" :label="term">{{ term }} 个月</el-radio-button>
            </el-radio-group>
            <el-input-number v-model="settingsForm.default_term_months" :min="1" :max="120" :step="1" controls-position="right" />
          </el-space>
        </el-form-item>
        <el-form-item label="提醒文字" class="remind-text-field">
          <el-input v-model="settingsForm.remind_text" maxlength="500" show-word-limit />
        </el-form-item>
        <el-form-item label=" ">
          <el-button type="primary" :icon="Setting" :loading="action === 'save-settings'" @click="saveSettings">保存</el-button>
        </el-form-item>
      </el-form>

      <el-table :data="memberships.sortedMembers" row-key="user_id" v-loading="memberships.loading" class="section-gap" empty-text="暂无成员">
        <el-table-column prop="display_name" label="成员" min-width="150" />
        <el-table-column prop="started_on" label="开始日期" width="125" />
        <el-table-column label="期限" width="100"><template #default="{ row }">{{ row.term_months }} 个月</template></el-table-column>
        <el-table-column prop="expires_on" label="到期日" width="125" />
        <el-table-column label="状态" width="105"><template #default="{ row }"><el-tag :type="statusType(row)">{{ statusText(row) }}</el-tag></template></el-table-column>
        <el-table-column prop="reminded_on" label="上次提醒" width="125"><template #default="{ row }">{{ row.reminded_on || '-' }}</template></el-table-column>
        <el-table-column prop="note" label="备注" min-width="150"><template #default="{ row }">{{ row.note || '-' }}</template></el-table-column>
        <el-table-column label="操作" width="190" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :icon="Edit" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="canRemind(row)" link type="warning" :icon="Bell" :loading="action === 'remind'" @click="remind(row)">再提醒一次</el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>
  </el-card>

  <el-dialog v-model="editDialog" title="编辑会员" width="520px" destroy-on-close>
    <el-alert v-if="editError" :title="editError" type="error" :closable="false" show-icon class="section-gap" />
    <el-form :model="editForm" label-position="top">
      <el-form-item label="开始日期"><el-date-picker v-model="editForm.started_on" type="date" value-format="YYYY-MM-DD" format="YYYY-MM-DD" class="full-width" /></el-form-item>
      <el-form-item label="期限">
        <el-space wrap>
          <el-radio-group v-model="editForm.term_months" size="small">
            <el-radio-button v-for="term in quickTerms" :key="term" :label="term">{{ term }} 个月</el-radio-button>
          </el-radio-group>
          <el-input-number v-model="editForm.term_months" :min="1" :max="120" :step="1" controls-position="right" />
        </el-space>
      </el-form-item>
      <el-form-item label="备注"><el-input v-model="editForm.note" type="textarea" :rows="3" maxlength="500" show-word-limit /></el-form-item>
      <el-form-item label="不提醒（例如群主本人）"><el-switch v-model="editForm.exempt" active-text="不提醒" inactive-text="提醒" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="editDialog = false">取消</el-button>
      <el-button type="primary" :loading="action === 'save-member'" @click="saveEdit">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.membership-empty { padding: 10px 0 4px; color: #738198; }
.membership-settings { display: grid; grid-template-columns: 180px minmax(280px, 1fr) minmax(260px, 1.4fr) auto; gap: 14px; align-items: end; }
.membership-settings .el-form-item { margin-bottom: 0; }
.remind-text-field { min-width: 0; }
@media (max-width: 900px) { .membership-settings { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 600px) { .membership-settings { grid-template-columns: 1fr; } }
</style>
