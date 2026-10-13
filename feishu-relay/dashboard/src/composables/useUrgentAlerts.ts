import { computed, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { urgentAlertsApi, type UrgentAlertRecipient, type UrgentAlertSettings } from '../api/urgent-alerts';

const emptySettings = (): UrgentAlertSettings => ({ enabled: false, chat_id: '', user_id: '', name: '' });

function copySettings(settings: UrgentAlertSettings): UrgentAlertSettings {
  return { ...settings };
}

function errorText(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

export function useUrgentAlerts() {
  const settings = ref<UrgentAlertSettings>(emptySettings());
  const savedSettings = ref<UrgentAlertSettings>(emptySettings());
  const recipients = ref<UrgentAlertRecipient[]>([]);
  const loading = ref(false);
  const saving = ref(false);
  const testing = ref(false);
  const error = ref('');

  const dirty = computed(() => JSON.stringify(settings.value) !== JSON.stringify(savedSettings.value));
  const hasRecipient = computed(() => Boolean(savedSettings.value.chat_id && savedSettings.value.user_id));
  const selectedRecipient = computed({
    get: () => settings.value.chat_id && settings.value.user_id ? `${settings.value.chat_id}:${settings.value.user_id}` : '',
    set: (key: string) => {
      const recipient = recipients.value.find((item) => `${item.chat_id}:${item.user_id}` === key);
      if (!recipient) return;
      settings.value = { ...settings.value, chat_id: recipient.chat_id, user_id: recipient.user_id, name: recipient.name };
    },
  });

  async function load() {
    loading.value = true;
    error.value = '';
    try {
      const [settingsResponse, recipientsResponse] = await Promise.all([urgentAlertsApi.settings(), urgentAlertsApi.recipients()]);
      settings.value = copySettings(settingsResponse.settings);
      savedSettings.value = copySettings(settingsResponse.settings);
      recipients.value = recipientsResponse.recipients;
    } catch (cause) {
      error.value = errorText(cause);
    } finally {
      loading.value = false;
    }
  }

  async function save() {
    saving.value = true;
    error.value = '';
    try {
      const response = await urgentAlertsApi.saveSettings(copySettings(settings.value));
      settings.value = copySettings(response.settings);
      savedSettings.value = copySettings(response.settings);
      ElMessage.success('加急设置已保存');
    } catch (cause) {
      error.value = errorText(cause);
      ElMessage.error(error.value);
    } finally {
      saving.value = false;
    }
  }

  async function sendTest() {
    if (dirty.value) {
      error.value = '请先保存加急设置，再发送测试加急';
      return;
    }
    if (!hasRecipient.value) {
      error.value = '请先选择加急接收人';
      return;
    }
    try {
      await ElMessageBox.confirm(
        `将向“${savedSettings.value.name || '接收人'}”的飞书发送一条测试加急，对方的飞书会响铃提醒。确认发送吗？`,
        '发送测试加急',
        { type: 'warning', confirmButtonText: '发送', cancelButtonText: '取消' },
      );
    } catch {
      return;
    }
    testing.value = true;
    error.value = '';
    try {
      await urgentAlertsApi.sendTest();
      ElMessage.success('测试加急已发送');
    } catch (cause) {
      error.value = errorText(cause);
      ElMessage.error(error.value);
    } finally {
      testing.value = false;
    }
  }

  return { settings, recipients, loading, saving, testing, error, dirty, hasRecipient, selectedRecipient, load, save, sendTest };
}
