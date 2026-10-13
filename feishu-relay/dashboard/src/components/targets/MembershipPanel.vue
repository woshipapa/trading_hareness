<script setup lang="ts">
import { computed, inject, reactive, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Bell, Edit, Refresh, Search, Setting } from '@element-plus/icons-vue';
import { targetGroupsContextKey } from '../../dashboard-context';
import { useMemberships } from '../../composables/useMemberships';
import type { MembershipRow, MembershipSuggestion } from '../../api/memberships';

const selection = inject(targetGroupsContextKey);
if (!selection) throw new Error('membership panel requires the target groups context');

const memberships = useMemberships(selection.selectedTarget);
const action = memberships.action;
const quickTerms = [1, 3, 6, 12];
const settingsForm = reactive({ enabled: false, default_term_months: 1, remind_text: '' });
const editDialog = ref(false);
const editError = ref('');
const editing = ref<MembershipRow | null>(null);
const editForm = reactive({ started_on: '', term_months: 1, fixed: false, expires_on: '', note: '', exempt: false });

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
    fixed: row.expiry_fixed === true,
    expires_on: row.expires_on,
    note: row.note ?? '',
    exempt: memberships.settings.value?.exempt_user_ids.includes(row.user_id) ?? false,
  });
  editDialog.value = true;
}

// Fills the form from what the alias says; nothing is saved until the person presses 保存.
function applySuggestion(suggestion: MembershipSuggestion) {
  if (suggestion.started_on) editForm.started_on = suggestion.started_on;
  if (suggestion.term_months) editForm.term_months = suggestion.term_months;
  editForm.fixed = Boolean(suggestion.expires_on);
  if (suggestion.expires_on) editForm.expires_on = suggestion.expires_on;
}

async function saveEdit() {
  if (!editing.value) return;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(editForm.started_on) || !Number.isInteger(editForm.term_months) || editForm.term_months < 1 || editForm.term_months > 120) {
    editError.value = '请填写有效的开始日期和 1–120 的整数期限';
    return;
  }
  if (editForm.fixed && (!/^\d{4}-\d{2}-\d{2}$/.test(editForm.expires_on ?? '') || editForm.expires_on < editForm.started_on)) {
    editError.value = '指定的到期日要晚于开始日期';
    return;
  }
  editError.value = '';
  const edit = { started_on: editForm.started_on, term_months: editForm.term_months, expires_on: editForm.fixed ? editForm.expires_on : null, note: editForm.note || null, exempt: editForm.exempt };
  if (await memberships.saveMember(editing.value.user_id, edit)) {
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
      <el-alert v-if="memberships.error.value" :title="memberships.error.value" type="error" :closable="false" show-icon class="section-gap" />
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

      <el-table :data="memberships.sortedMembers.value" row-key="user_id" v-loading="memberships.loading.value" class="section-gap" empty-text="暂无成员">
        <el-table-column prop="display_name" label="成员" min-width="130" />
        <el-table-column label="备注名" min-width="190"><template #default="{ row }">{{ row.alias || '-' }}</template></el-table-column>
        <el-table-column prop="started_on" label="开始日期" width="125" />
        <el-table-column label="期限" width="100"><template #default="{ row }">{{ row.expiry_fixed ? '指定到期日' : `${row.term_months} 个月` }}</template></el-table-column>
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
      <el-form-item v-if="editing?.alias" :label="`备注名：${editing.alias}`">
        <el-space v-if="editing.suggestions?.length" wrap>
          <span class="membership-hint">按备注名填：</span>
          <el-button v-for="suggestion in editing.suggestions" :key="suggestion.label" size="small" plain @click="applySuggestion(suggestion)">{{ suggestion.label }}</el-button>
        </el-space>
        <span v-else class="membership-hint">备注名里没有读出日期或期限</span>
      </el-form-item>
      <el-form-item label="开始日期"><el-date-picker v-model="editForm.started_on" type="date" value-format="YYYY-MM-DD" format="YYYY-MM-DD" class="full-width" /></el-form-item>
      <el-form-item label="期限">
        <el-space wrap>
          <el-radio-group v-model="editForm.term_months" size="small">
            <el-radio-button v-for="term in quickTerms" :key="term" :label="term">{{ term }} 个月</el-radio-button>
          </el-radio-group>
          <el-input-number v-model="editForm.term_months" :min="1" :max="120" :step="1" controls-position="right" />
        </el-space>
      </el-form-item>
      <el-form-item label="到期日">
        <el-space wrap>
          <el-switch v-model="editForm.fixed" active-text="指定到期日" inactive-text="按期限计算" />
          <el-date-picker v-if="editForm.fixed" v-model="editForm.expires_on" type="date" value-format="YYYY-MM-DD" format="YYYY-MM-DD" placeholder="到期日" />
          <span v-else class="membership-hint">开始日期加期限</span>
        </el-space>
      </el-form-item>
      <el-form-item label="备注"><el-input v-model="editForm.note" type="textarea" :rows="3" maxlength="500" show-word-limit /></el-form-item>
      <el-form-item label="不提醒（例如管理员或免费成员）"><el-switch v-model="editForm.exempt" active-text="不提醒" inactive-text="提醒" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="editDialog = false">取消</el-button>
      <el-button type="primary" :loading="action === 'save-member'" @click="saveEdit">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.membership-empty { padding: 10px 0 4px; color: #738198; }
.membership-hint { color: #738198; font-size: 13px; }
.membership-settings { display: grid; grid-template-columns: 180px minmax(280px, 1fr) minmax(260px, 1.4fr) auto; gap: 14px; align-items: end; }
.membership-settings .el-form-item { margin-bottom: 0; }
.remind-text-field { min-width: 0; }
@media (max-width: 900px) { .membership-settings { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 600px) { .membership-settings { grid-template-columns: 1fr; } }
</style>
