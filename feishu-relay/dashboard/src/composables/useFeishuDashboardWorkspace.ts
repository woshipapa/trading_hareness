import { computed, onBeforeUnmount, onMounted, provide, proxyRefs, ref } from 'vue';
import { getJson } from '../api/http';
import { useFeishuRelayWorkspace } from './useFeishuRelayWorkspace';
import { feishuWorkbenchContextKey, groupRelayMonitorContextKey } from '../dashboard-context';

type Route = { tag: string; label: string };
type EventItem = {
  event_id: string;
  received_at: string;
  message_type?: string;
  text?: string;
  source_label?: string;
  n8n_status?: string;
  n8n_error?: string | null;
};

type DashboardSection = 'monitor' | 'workbench' | 'relay';

export function useFeishuDashboardWorkspace() {
  const mobileMediaQuery = window.matchMedia('(max-width: 760px)');
  const mobileLayout = ref(mobileMediaQuery.matches);
  const activeSection = ref<DashboardSection>(initialSection(window.location.pathname));
  const routes = ref<Route[]>([]);
  const events = ref<EventItem[]>([]);
  const connected = ref(false);
  const eventFilter = ref('all');
  const relayTag = ref('');
  const relaySource = ref('');
  const relayText = ref('');
  const relayFiles = ref<File[]>([]);
  const relayDate = ref('');
  const relayTime = ref('');
  const relayState = ref('');
  const relayProgress = ref(0);
  const relayXhr = ref<XMLHttpRequest | null>(null);
  let eventSource: EventSource | null = null;
  let retryTimer: number | null = null;
  let retryDelay = 1000;
  let refreshTimer: number | null = null;
  const syncMobileLayout = (event: MediaQueryListEvent) => { mobileLayout.value = event.matches; };

  const feishuRelayWorkspace = useFeishuRelayWorkspace();
  const visibleEvents = computed(() => eventFilter.value === 'all'
    ? events.value
    : events.value.filter((item) => item.n8n_status === eventFilter.value));
  const dateText = (value?: string | null) => value ? new Date(value).toLocaleString() : '未运行';
  const ageText = (seconds?: number | null) => seconds === null || seconds === undefined
    ? '-'
    : seconds < 60 ? `${Math.round(seconds)} 秒`
      : seconds < 3600 ? `${(seconds / 60).toFixed(1)} 分钟`
        : `${(seconds / 3600).toFixed(1)} 小时`;

  async function loadConfig() {
    try {
      const data = await getJson<{ routes?: Route[] }>('/api/config');
      routes.value = data.routes ?? [];
      relayTag.value ||= routes.value[0]?.tag ?? '';
    } catch (error) {
      relayState.value = error instanceof Error ? error.message : String(error);
    }
  }

  function connectEvents() {
    eventSource?.close();
    eventSource = new EventSource('/events');
    eventSource.addEventListener('snapshot', (event) => {
      events.value = JSON.parse((event as MessageEvent).data);
      connected.value = true;
    });
    eventSource.addEventListener('message', (event) => {
      const item: EventItem = JSON.parse((event as MessageEvent).data);
      events.value = [item, ...events.value.filter((current) => current.event_id !== item.event_id)].slice(0, 200);
      connected.value = true;
    });
    eventSource.onopen = () => { connected.value = true; retryDelay = 1000; };
    eventSource.onerror = () => {
      connected.value = false;
      eventSource?.close();
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(connectEvents, retryDelay);
      retryDelay = Math.min(30_000, retryDelay * 2);
    };
  }

  function selectActiveSection(section: string) {
    if (!['monitor', 'workbench', 'relay'].includes(section)) return;
    activeSection.value = section as DashboardSection;
    localStorage.setItem('feishu-relay-active-section', section);
    window.history.replaceState({}, '', section === 'monitor' ? '/monitor' : `/${section}`);
    void loadActiveSection();
  }

  function loadActiveSection() {
    if (activeSection.value === 'monitor') {
      void feishuRelayWorkspace.loadExportBookmarks();
      return feishuRelayWorkspace.loadGroupRelayStatus();
    }
    if (activeSection.value === 'workbench') return feishuRelayWorkspace.loadFeishuWorkbench();
    return Promise.resolve();
  }

  function addFiles(list: FileList | File[]) {
    const incoming = Array.from(list);
    const allowed = incoming.filter((file) => file.size <= 500 * 1024 * 1024);
    if (allowed.length !== incoming.length) relayState.value = '超过 500 MB 的文件未加入';
    relayFiles.value = [...relayFiles.value, ...allowed.filter((file) => !relayFiles.value.some((current) => current.name === file.name && current.size === file.size))];
  }

  function submitRelay() {
    if ((!relayText.value.trim() && !relayFiles.value.length) || !relayTag.value) {
      relayState.value = '请填写正文或选择媒体，并选择来源';
      return;
    }
    const form = new FormData();
    form.append('tag', relayTag.value);
    form.append('text', relayText.value.trim());
    form.append('source_label', relaySource.value.trim());
    if (relayDate.value) form.append('content_date', relayDate.value);
    if (relayTime.value) form.append('content_time', relayTime.value);
    relayFiles.value.forEach((file) => form.append('media', file, file.name));
    const xhr = new XMLHttpRequest();
    relayXhr.value = xhr;
    relayState.value = '上传中';
    relayProgress.value = 0;
    xhr.open('POST', '/manual-relay');
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) relayProgress.value = Math.round(event.loaded / event.total * 100); };
    xhr.onload = () => {
      try {
        const body = JSON.parse(xhr.responseText);
        if (xhr.status >= 300) throw new Error(body.message);
        relayState.value = `已接收 ${body.message_id}`;
        relayText.value = '';
        relayFiles.value = [];
      } catch (error) {
        relayState.value = `失败：${error instanceof Error ? error.message : String(error)}`;
      }
      relayXhr.value = null;
    };
    xhr.onerror = () => { relayState.value = '网络错误'; relayXhr.value = null; };
    xhr.send(form);
  }

  const dashboardBindings = {
    activeSection, routes, events, connected, eventFilter, visibleEvents, mobileLayout,
    relayTag, relaySource, relayText, relayFiles, relayDate, relayTime, relayState, relayProgress, relayXhr,
    selectActiveSection, loadGroupRelayStatus: feishuRelayWorkspace.loadGroupRelayStatus,
    loadFeishuWorkbench: feishuRelayWorkspace.loadFeishuWorkbench,
    addFiles, submitRelay, dateText, ageText,
  };

  provide('manual-relay', dashboardBindings);
  provide(feishuWorkbenchContextKey, { ...feishuRelayWorkspace, mobileLayout, dateText });
  provide(groupRelayMonitorContextKey, { ...feishuRelayWorkspace, mobileLayout, eventFilter, visibleEvents, dateText, ageText });

  onMounted(() => {
    mobileMediaQuery.addEventListener('change', syncMobileLayout);
    void loadConfig();
    connectEvents();
    void loadActiveSection();
    refreshTimer = window.setInterval(() => { void loadActiveSection(); }, 10_000);
  });
  onBeforeUnmount(() => {
    eventSource?.close();
    if (retryTimer !== null) window.clearTimeout(retryTimer);
    if (refreshTimer !== null) window.clearInterval(refreshTimer);
    mobileMediaQuery.removeEventListener('change', syncMobileLayout);
  });

  return proxyRefs({ ...dashboardBindings, ...feishuRelayWorkspace });
}

function initialSection(path: string): DashboardSection {
  if (path === '/workbench') return 'workbench';
  if (path === '/relay') return 'relay';
  const persisted = localStorage.getItem('feishu-relay-active-section');
  return persisted === 'workbench' || persisted === 'relay' || persisted === 'monitor' ? persisted : 'monitor';
}
