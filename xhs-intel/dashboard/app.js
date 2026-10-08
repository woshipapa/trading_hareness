const iconPaths = {
  layout: '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>',
  sparkles: '<path d="m12 3-1.9 4.8a2 2 0 0 1-1.1 1.1L4.2 11 9 12.9a2 2 0 0 1 1.1 1.1l1.9 4.8 1.9-4.8a2 2 0 0 1 1.1-1.1l4.8-1.9L15 9.1A2 2 0 0 1 13.9 8Z"/><path d="M5 3v4M3 5h4M19 17v4M17 19h4"/>',
  tags: '<path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42Z"/><circle cx="7.5" cy="7.5" r=".5" fill="currentColor"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
  refresh: '<path d="M20 11a8.1 8.1 0 0 0-15.5-2M4 4v5h5M4 13a8.1 8.1 0 0 0 15.5 2M20 20v-5h-5"/>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5M3 12c0 1.7 4 3 9 3s9-1.3 9-3"/>',
  cpu: '<rect width="16" height="16" x="4" y="4" rx="2"/><rect width="6" height="6" x="9" y="9" rx="1"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/>',
  send: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="3"/><path d="m16 16-1.9-1.9"/>',
  play: '<polygon points="6 3 20 12 6 21 6 3"/>',
  'user-plus': '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M19 8v6M22 11h-6"/>',
  plus: '<path d="M5 12h14M12 5v14"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  save: '<path d="M15.2 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8.8Z"/><path d="M17 21v-8H7v8M7 3v5h8"/>',
  close: '<path d="M18 6 6 18M6 6l12 12"/>',
  external: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  check: '<path d="m20 6-11 11-5-5"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 15H6L5 6M10 11v6M14 11v6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/>',
  inbox: '<path d="M4 4h16v14H4z"/><path d="M4 13h4l2 3h4l2-3h4"/>',
  terminal: '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m6 9 3 3-3 3M13 15h5"/>',
  message: '<path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z"/><path d="M8 10h8M8 14h5"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.1 1.1"/><path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.1-1.1"/>',
};

function icon(name, label = '') {
  const path = iconPaths[name] || iconPaths.alert;
  const title = label ? `<title>${escapeHtml(label)}</title>` : '';
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="${label ? 'false' : 'true'}">${title}${path}</svg>`;
}

function hydrateIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach((node) => {
    node.innerHTML = icon(node.dataset.icon);
  });
}

const state = {
  activeView: 'overview',
  followingTab: 'watch',
  dashboard: null,
  jobs: [],
  watchUsers: [],
  candidates: [],
  following: [],
  followingTotal: 0,
  selectedRunId: '',
  recommendationItems: [],
  capabilities: [],
  deliveries: [],
  singleJobs: [],
  singleDraft: '',
  singleDeliver: false,
  operationDraft: { operation: 'pc.search_some_note', args: '["AI Infra", 5]', kwargs: '{}', confirm: false, deliver: false },
  operationResult: null,
  feishuDraft: '',
  loading: true,
  error: '',
};

const pageMeta = {
  overview: ['运行总览', '采集、AI 筛选与飞书投递状态'],
  recommendations: ['推荐筛选', '每日推荐流的候选、判断与摘要状态'],
  single: ['单篇解析', '抓取指定文章并由本机 AI 独立分析'],
  topics: ['关注主题', '管理可版本化的内容筛选策略'],
  following: ['关注账号', '维护监控名单并审核关注候选'],
  capabilities: ['能力控制台', '调用与飞书指令一致的 Spider_XHS 受控方法'],
  feishu: ['飞书交互', '向小红书同步群投递消息并查看发送台账'],
};

const content = document.querySelector('#content');
const refreshButton = document.querySelector('#refresh-button');
const topicDialog = document.querySelector('#topic-dialog');
const watchDialog = document.querySelector('#watch-dialog');
let toastTimer;

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function safeXhsUrl(value) {
  try {
    const url = new URL(String(value || ''));
    const host = url.hostname.toLowerCase();
    if (url.protocol === 'https:' && (host === 'xiaohongshu.com' || host.endsWith('.xiaohongshu.com'))) {
      return escapeHtml(url.href);
    }
  } catch (_) {
    // Invalid or incomplete upstream URLs are rendered as inert links.
  }
  return '#';
}

function safeStatus(value) {
  return String(value || 'unknown').toLowerCase().replace(/[^a-z0-9_-]/g, '');
}

function statusLabel(value) {
  const labels = {
    pending: '待处理', processing: '处理中', ready: '待投递', sent: '已投递', failed: '失败', filter_failed: '筛选失败',
    completed: '已完成', running: '运行中', blocked: '已阻断', partial: '部分完成', collected: '已采集',
    filter_queued: '待筛选', summary_queued: '待总结', summary_ready: '摘要就绪', include: '入选', review: '复核',
    exclude: '排除', candidate: '候选', active: '监控中', rejected: '已拒绝', disabled: '已停用', ok: '正常',
  };
  return labels[value] || value || '未知';
}

function pill(value) {
  return `<span class="status-pill ${safeStatus(value)}">${escapeHtml(statusLabel(value))}</span>`;
}

function formatTime(value) {
  if (!value) return '暂无';
  const numeric = typeof value === 'number' ? value * 1000 : Number(value) > 1e12 ? Number(value) : null;
  const date = numeric ? new Date(numeric) : new Date(value);
  if (Number.isNaN(date.getTime())) return '未知';
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date);
}

function timeAgo(value) {
  if (!value) return '未连接';
  const then = Number(value) * 1000;
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

function jsonValue(value, fallback) {
  try { return JSON.parse(value || ''); } catch { return fallback; }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  let payload = {};
  try { payload = await response.json(); } catch { payload = {}; }
  if (response.status === 401) {
    window.location.reload();
    throw new Error('控制台会话已刷新');
  }
  if (!response.ok) throw new Error(payload.error || payload.status || `HTTP ${response.status}`);
  return payload;
}

function post(path, payload = {}) {
  return api(path, { method: 'POST', body: JSON.stringify(payload) });
}

function toast(message, type = 'ok') {
  const node = document.querySelector('#toast');
  node.textContent = message;
  node.className = `toast show${type === 'error' ? ' error' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.className = 'toast'; }, 3200);
}

function emptyState(title, detail) {
  return `<div class="empty">${icon('inbox')}<strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span></div>`;
}

function metric(label, value, detail, iconName) {
  return `<article class="metric"><div class="metric-label"><span>${escapeHtml(label)}</span>${icon(iconName)}</div><div class="metric-value">${escapeHtml(value)}</div><div class="metric-detail">${escapeHtml(detail)}</div></article>`;
}

function sectionHeader(title, detail = '', actions = '') {
  return `<div class="panel-header"><div><h2>${escapeHtml(title)}</h2>${detail ? `<p>${escapeHtml(detail)}</p>` : ''}</div>${actions ? `<div class="panel-actions">${actions}</div>` : ''}</div>`;
}

async function refreshAll({ quiet = false } = {}) {
  if (!quiet) {
    state.loading = true;
    refreshButton.disabled = true;
    refreshButton.querySelector('span:last-child').textContent = '刷新中';
    render();
  }
  try {
    const [dashboard, jobs, watch, candidates, following, capabilities, feishu, singleNotes] = await Promise.all([
      api('/v1/dashboard'),
      api('/v1/jobs?limit=40'),
      api('/v1/watch-users?enabled=all'),
      api('/v1/following/candidates?limit=100'),
      api('/v1/following?limit=100'),
      api('/v1/capabilities'),
      api('/v1/feishu/status?limit=50'),
      api('/v1/single-notes?limit=20'),
    ]);
    state.dashboard = dashboard;
    state.jobs = jobs.jobs || [];
    state.watchUsers = watch.users || [];
    state.candidates = candidates.candidates || [];
    state.following = following.accounts || [];
    state.followingTotal = following.total || 0;
    state.capabilities = capabilities.namespaces || [];
    state.deliveries = feishu.deliveries || [];
    state.singleJobs = singleNotes.jobs || [];
    state.error = '';
    const runs = dashboard.recommendation_runs || [];
    if (!state.selectedRunId && runs.length) state.selectedRunId = runs[0].run_id;
    if (state.selectedRunId && runs.some((run) => run.run_id === state.selectedRunId)
        && state.activeView === 'recommendations') {
      await loadRecommendationItems(state.selectedRunId, false);
    }
    updateConnection(true);
    document.querySelector('#last-updated').textContent = `更新于 ${new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date())}`;
  } catch (error) {
    state.error = error.message || String(error);
    updateConnection(false);
    if (quiet) toast(`刷新失败：${state.error}`, 'error');
  } finally {
    state.loading = false;
    refreshButton.disabled = false;
    refreshButton.querySelector('span:last-child').textContent = '刷新';
    render();
  }
}

async function loadRecommendationItems(runId, rerender = true) {
  state.selectedRunId = runId;
  try {
    const result = await api(`/v1/recommendations/items?run_id=${encodeURIComponent(runId)}`);
    state.recommendationItems = result.items || [];
    state.error = '';
  } catch (error) {
    state.recommendationItems = [];
    state.error = error.message || String(error);
  }
  if (rerender) render();
}

function updateConnection(healthy) {
  const dot = document.querySelector('#side-status-dot');
  dot.className = `status-dot ${healthy ? 'healthy' : 'warning'}`;
  document.querySelector('#side-status-label').textContent = healthy ? 'Edge 服务正常' : 'Edge 服务异常';
  document.querySelector('#side-status-detail').textContent = healthy ? '控制台会话已连接' : '数据读取失败';
}

function renderOverview() {
  const data = state.dashboard;
  if (!data) return emptyState('尚未取得运行状态', '刷新后重新读取 Edge 服务。');
  const queue = data.jobs || {};
  const pending = (queue.pending || 0) + (queue.processing || 0) + (queue.ready || 0);
  const workerFresh = data.last_worker_seen && Date.now() / 1000 - data.last_worker_seen < 300;
  const healthy = data.cookie_configured && data.feishu_webhook_configured;
  const recentRuns = (data.recommendation_runs || []).slice(0, 6);
  const recentJobs = state.jobs.slice(0, 7);
  return `
    <div class="metric-grid">
      ${metric('已采集笔记', data.notes || 0, `${data.revisions || 0} 个内容版本`, 'database')}
      ${metric('推荐扫描', (data.recommendation_runs || []).length, `${data.following?.total || 0} 个关注快照`, 'sparkles')}
      ${metric('监控账号', data.watch_users || 0, `${(data.topics || []).filter((item) => item.enabled).length} 个启用主题`, 'users')}
      ${metric('队列待办', pending, `${queue.failed || 0} 个失败任务`, 'clock')}
    </div>
    <section class="panel">
      ${sectionHeader('自动化链路', 'Edge 持续采集，本机 AI worker 完成筛选与总结')}
      <div class="pipeline">
        <div class="pipeline-step ${data.cookie_configured ? 'ok' : 'warn'}"><div class="pipeline-icon">${icon('scan')}</div><strong>小红书采集</strong><span>${data.cookie_configured ? 'Cookie 已配置' : 'Cookie 未就绪'}</span></div>
        <div class="pipeline-step ok"><div class="pipeline-icon">${icon('database')}</div><strong>Edge 队列</strong><span>${pending} 个任务等待流转</span></div>
        <div class="pipeline-step ${workerFresh ? 'ok' : 'warn'}"><div class="pipeline-icon">${icon('cpu')}</div><strong>本机 AI</strong><span>${workerFresh ? `最近心跳 ${timeAgo(data.last_worker_seen)}` : '等待本机 worker 心跳'}</span></div>
        <div class="pipeline-step ${data.feishu_webhook_configured ? 'ok' : 'warn'}"><div class="pipeline-icon">${icon('send')}</div><strong>飞书投递</strong><span>${data.feishu_webhook_configured ? `${queue.sent || 0} 个任务已投递` : 'Webhook 未配置'}</span></div>
      </div>
    </section>
    <div class="two-column">
      <section class="panel">
        ${sectionHeader('最近推荐扫描', '', `<button class="button small primary" data-action="run-recommendations">${icon('play')}<span>扫描 50 条</span></button>`)}
        <div class="panel-body flush">${recentRuns.length ? `<div class="table-wrap"><table><thead><tr><th>运行</th><th>状态</th><th>抓取</th><th>入选</th><th>时间</th></tr></thead><tbody>${recentRuns.map((run) => `<tr><td class="mono ellipsis">${escapeHtml(run.run_id)}</td><td>${pill(run.status)}</td><td class="number">${run.fetched || 0}</td><td class="number">${run.selected || 0}</td><td>${formatTime(run.started)}</td></tr>`).join('')}</tbody></table></div>` : emptyState('暂无推荐扫描', '定时任务运行后会出现在这里。')}</div>
      </section>
      <section class="panel">
        ${sectionHeader('最近任务')}
        <div class="panel-body flush">${recentJobs.length ? `<div class="table-wrap"><table><thead><tr><th>类型</th><th>状态</th><th>更新</th></tr></thead><tbody>${recentJobs.map((job) => `<tr><td><span class="row-title">${escapeHtml(job.job_type || 'summary')}</span><span class="row-subtitle mono">${escapeHtml(job.job_id)}</span></td><td>${pill(job.status)}</td><td>${formatTime(job.updated)}</td></tr>`).join('')}</tbody></table></div>` : emptyState('任务队列为空', '新的采集结果会自动进入队列。')}</div>
      </section>
    </div>
    ${!healthy ? '<div class="error-banner">采集 Cookie 或飞书投递配置未完成，自动化链路不会完整闭环。</div>' : ''}`;
}

function renderRecommendations() {
  const runs = state.dashboard?.recommendation_runs || [];
  const selected = runs.find((run) => run.run_id === state.selectedRunId);
  const items = state.recommendationItems;
  const retryAction = selected?.status === 'filter_failed'
    ? `<button class="button secondary" data-action="retry-recommendation" data-run-id="${escapeHtml(selected.run_id)}">${icon('refresh')}<span>重试 AI 筛选</span></button>`
    : '';
  const action = `${retryAction}<button class="button primary" data-action="run-recommendations">${icon('play')}<span>扫描 50 条</span></button>`;
  const list = runs.length ? runs.map((run) => `<button type="button" class="run-option ${run.run_id === state.selectedRunId ? 'active' : ''}" data-action="select-run" data-run-id="${escapeHtml(run.run_id)}"><div class="run-option-top"><strong>${escapeHtml(run.run_id)}</strong>${pill(run.status)}</div><div class="run-stats"><span>抓取 ${run.fetched || 0}</span><span>入选 ${run.selected || 0}</span><span>复核 ${run.review || 0}</span></div></button>`).join('') : emptyState('暂无运行记录', '启动推荐扫描后会生成运行记录。');
  const itemRows = items.map((item) => `<tr><td class="number">${item.source_rank || '-'}</td><td><a class="row-title" href="${safeXhsUrl(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title || '无标题')}</a><span class="row-subtitle">${escapeHtml(item.author || item.text || '作者未知')}</span></td><td>${pill(item.decision || item.state)}</td><td><div class="score">${item.relevance_score == null ? '-' : `${Math.round(item.relevance_score * 100)}%`}</div>${item.relevance_score == null ? '' : `<progress class="score-track" max="100" value="${Math.max(0, Math.min(100, Number(item.relevance_score) * 100))}" aria-label="相关度"></progress>`}</td><td class="ellipsis" title="${escapeHtml(item.reason)}">${escapeHtml(item.reason || '等待 AI 判断')}</td><td><a class="icon-button" href="${safeXhsUrl(item.url)}" target="_blank" rel="noreferrer" title="打开原文" aria-label="打开原文">${icon('external')}</a></td></tr>`).join('');
  return `<div class="recommendation-layout">
    <aside class="run-list"><div class="run-list-header"><h2>最近运行</h2></div>${list}</aside>
    <section class="panel">
      ${sectionHeader(selected ? `候选内容 · ${statusLabel(selected.status)}` : '候选内容', selected ? `抓取 ${selected.fetched || 0}，入选 ${selected.selected || 0}，复核 ${selected.review || 0}，排除 ${selected.rejected || 0}` : '选择一次运行查看筛选结果', action)}
      <div class="panel-body flush">${itemRows ? `<div class="table-wrap"><table><thead><tr><th>序号</th><th>内容</th><th>判断</th><th>相关度</th><th>依据</th><th></th></tr></thead><tbody>${itemRows}</tbody></table></div>` : emptyState('暂无候选结果', selected ? '采集或 AI 筛选完成后会显示详细结果。' : '先选择一次推荐扫描。')}</div>
    </section>
  </div>`;
}

function renderSingleNotes() {
  const configured = Boolean(state.dashboard?.feishu_webhook_configured);
  const rows = state.singleJobs.map((job) => {
    const delivery = job.deliver_to_feishu
      ? `<span class="status-pill ${job.status === 'sent' ? 'sent' : 'pending'}">${job.status === 'sent' ? '飞书已投递' : '等待飞书'}</span>`
      : '<span class="status-pill">仅网页</span>';
    const result = job.summary
      ? `<div class="analysis-result">${escapeHtml(job.summary)}</div>`
      : `<div class="analysis-pending">${job.last_error ? `处理失败：${escapeHtml(job.last_error)}` : '等待 AI 解析'}</div>`;
    return `<article class="analysis-row">
      <div class="analysis-head">
        <div><a class="analysis-title" href="${safeXhsUrl(job.url)}" target="_blank" rel="noreferrer">${escapeHtml(job.title || '无标题')}</a><div class="analysis-meta"><span>${escapeHtml(job.author || '作者未知')}</span><span>${formatTime(job.published_at)}</span><span class="mono">${escapeHtml(job.note_id)}</span></div></div>
        <div class="analysis-status">${pill(job.status)}${delivery}</div>
      </div>
      ${result}
      <div class="analysis-footer"><span>${job.fetch_source === 'cache' ? '历史缓存' : '实时抓取'}</span><span>${job.model ? `模型 ${escapeHtml(job.model)}` : `尝试 ${job.attempts || 0} 次`}</span><span>更新 ${formatTime(job.updated)}</span>${job.delivered_parts ? `<span>飞书 ${job.delivered_parts} 个分片</span>` : ''}</div>
    </article>`;
  }).join('');
  return `<div class="single-layout">
    <section class="panel">
      ${sectionHeader('提交文章', '支持小红书文章地址或复制的分享文本')}
      <div class="panel-body single-form">
        <label><span>文章链接</span><textarea id="single-note-url" rows="5" placeholder="https://www.xiaohongshu.com/explore/...">${escapeHtml(state.singleDraft)}</textarea></label>
        <label class="single-check"><input id="single-note-deliver" type="checkbox" ${state.singleDeliver ? 'checked' : ''} ${configured ? '' : 'disabled'} /><span>解析完成后同步到飞书</span></label>
        <button class="button primary" data-action="analyze-single">${icon('sparkles')}<span>抓取并解析</span></button>
      </div>
    </section>
    <section class="panel">
      ${sectionHeader('最近解析', `${state.singleJobs.length} 个任务`)}
      <div class="analysis-list">${rows || emptyState('暂无单篇解析', '提交文章后，任务状态和 AI 结果会显示在这里。')}</div>
    </section>
  </div>`;
}

function renderTopics() {
  const topics = state.dashboard?.topics || [];
  const rows = topics.map((topic) => `<div class="topic-row"><div><strong>${escapeHtml(topic.name)}</strong><code>${escapeHtml(topic.slug)} · v${topic.active_version}</code></div><div class="topic-description">${escapeHtml(topic.description || topic.policy?.description || '暂无描述')}</div><button type="button" role="switch" aria-checked="${Boolean(topic.enabled)}" aria-label="${topic.enabled ? '停用' : '启用'} ${escapeHtml(topic.name)}" title="${topic.enabled ? '停用主题' : '启用主题'}" class="switch ${topic.enabled ? 'on' : ''}" data-action="toggle-topic" data-slug="${escapeHtml(topic.slug)}" data-enabled="${topic.enabled ? '1' : '0'}"></button><div class="topic-actions"><button type="button" class="button small secondary" data-action="edit-topic" data-slug="${escapeHtml(topic.slug)}">${icon('edit')}<span>编辑</span></button></div></div>`).join('');
  return `<section class="panel">
    ${sectionHeader('主题策略', `${topics.filter((item) => item.enabled).length} 个启用，${topics.length} 个版本入口`, `<button class="button primary" data-action="new-topic">${icon('plus')}<span>新增主题</span></button>`)}
    <div class="topic-list">${rows || emptyState('暂无主题', '新增主题后，推荐筛选会读取最新启用版本。')}</div>
  </section>`;
}

function renderWatchUsers() {
  const rows = state.watchUsers.filter((user) => user.enabled).map((user) => `<tr><td><span class="row-title">${escapeHtml(user.label || user.user_id)}</span><span class="row-subtitle mono">${escapeHtml(user.user_id)}</span></td><td>${escapeHtml(user.source || 'manual')}</td><td>${pill(user.state || 'active')}</td><td>${formatTime(user.updated)}</td><td><div class="table-actions"><button class="button small danger" data-action="remove-watch" data-user-id="${escapeHtml(user.user_id)}">${icon('trash')}<span>移除</span></button></div></td></tr>`).join('');
  return rows ? `<div class="table-wrap"><table><thead><tr><th>账号</th><th>来源</th><th>状态</th><th>更新</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState('监控名单为空', '从候选中通过账号，或直接加入用户主页。');
}

function renderCandidates() {
  const rows = state.candidates.map((candidate) => {
    const score = jsonValue(candidate.score_json, {});
    const reviewed = Boolean(candidate.reviewed_at);
    const actions = reviewed ? `<span class="muted">${formatTime(candidate.reviewed_at)}</span>` : candidate.decision === 'exclude' ? `<button class="button small danger" data-action="reject-candidate" data-user-id="${escapeHtml(candidate.user_id)}">${icon('close')}<span>确认排除</span></button>` : `<button class="button small primary" data-action="approve-candidate" data-user-id="${escapeHtml(candidate.user_id)}">${icon('check')}<span>加入监控</span></button><button class="button small danger" data-action="reject-candidate" data-user-id="${escapeHtml(candidate.user_id)}">${icon('close')}<span>排除</span></button>`;
    return `<tr><td><span class="row-title">${escapeHtml(candidate.nickname || candidate.user_id)}</span><span class="row-subtitle mono">${escapeHtml(candidate.user_id)}</span></td><td>${pill(candidate.decision)}</td><td class="score">${score.score == null ? '-' : `${Math.round(score.score * 100)}%`}</td><td class="ellipsis" title="${escapeHtml(candidate.reason)}">${escapeHtml(candidate.reason || '暂无判断依据')}</td><td><div class="table-actions">${actions}</div></td></tr>`;
  }).join('');
  return rows ? `<div class="table-wrap"><table><thead><tr><th>账号</th><th>AI 判断</th><th>得分</th><th>依据</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState('暂无筛选结果', '同步关注快照后运行账号筛选。');
}

function renderFollowingSnapshot() {
  const rows = state.following.map((account) => `<tr><td><span class="row-title">${escapeHtml(account.nickname || account.user_id)}</span><span class="row-subtitle mono">${escapeHtml(account.user_id)}</span></td><td>${pill(account.state)}</td><td class="mono ellipsis">${escapeHtml(account.source_endpoint)}</td><td>${formatTime(account.last_seen)}</td></tr>`).join('');
  return `${state.followingTotal > state.following.length ? `<div class="error-banner">当前显示最近 ${state.following.length} 个账号，共 ${state.followingTotal} 个。</div>` : ''}${rows ? `<div class="table-wrap"><table><thead><tr><th>账号</th><th>内部状态</th><th>来源</th><th>最近同步</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState('关注快照为空', '运行只读同步后会显示当前关注账号。')}`;
}

function renderFollowing() {
  const tabs = `<div class="tabs" role="tablist"><button class="tab ${state.followingTab === 'watch' ? 'active' : ''}" data-action="following-tab" data-tab="watch">监控名单</button><button class="tab ${state.followingTab === 'candidates' ? 'active' : ''}" data-action="following-tab" data-tab="candidates">筛选候选</button><button class="tab ${state.followingTab === 'snapshot' ? 'active' : ''}" data-action="following-tab" data-tab="snapshot">关注快照</button></div>`;
  let body = renderWatchUsers();
  let actions = `<button class="button secondary" data-action="run-watch">${icon('play')}<span>扫描名单</span></button><button class="button primary" data-action="new-watch">${icon('user-plus')}<span>加入账号</span></button>`;
  if (state.followingTab === 'candidates') {
    body = renderCandidates();
    actions = `<button class="button primary" data-action="screen-following">${icon('scan')}<span>筛选 50 个</span></button>`;
  } else if (state.followingTab === 'snapshot') {
    body = renderFollowingSnapshot();
    actions = `<button class="button primary" data-action="sync-following">${icon('refresh')}<span>同步关注</span></button>`;
  }
  return `<section class="panel">${sectionHeader('账号池', `${state.watchUsers.filter((user) => user.enabled).length} 个监控账号，${state.followingTotal} 个关注快照`, actions)}<div class="panel-body">${tabs}</div><div class="panel-body flush">${body}</div></section>`;
}

const operationPresets = {
  search: { operation: 'pc.search_some_note', args: '["AI Infra", 5]', kwargs: '{}' },
  feed: { operation: 'pc.get_homefeed_recommend_by_num', args: '["homefeed_recommend", 20]', kwargs: '{}' },
  me: { operation: 'pc.get_user_me', args: '[]', kwargs: '{}' },
  creator: { operation: 'creator.get_all_posted_notes', args: '[]', kwargs: '{}' },
  chats: { operation: 'live.get_chats', args: '[]', kwargs: '{"limit": 20}' },
};

function renderCapabilities() {
  const methods = state.capabilities.flatMap((namespace) => namespace.methods || []);
  const selected = methods.find((method) => method.operation === state.operationDraft.operation);
  const options = state.capabilities.map((namespace) => `<optgroup label="${escapeHtml(namespace.name)}">${(namespace.methods || []).map((method) => `<option value="${escapeHtml(method.operation)}" ${method.operation === state.operationDraft.operation ? 'selected' : ''}>${escapeHtml(method.name)}${method.mutating ? ' · 写操作' : ''}</option>`).join('')}</optgroup>`).join('');
  const namespaceSummary = state.capabilities.map((namespace) => `<span class="status-pill">${escapeHtml(namespace.name)} · ${(namespace.methods || []).length}</span>`).join('');
  const result = state.operationResult == null ? '运行结果会显示在这里。' : JSON.stringify(state.operationResult, null, 2);
  return `
    <section class="panel">
      ${sectionHeader('Spider_XHS 操作台', `${methods.length} 个注册方法，写操作需要再次确认`)}
      <div class="panel-body">
        <div class="quick-actions">
          <button class="button small secondary" data-action="operation-preset" data-preset="search">${icon('scan')}<span>搜索笔记</span></button>
          <button class="button small secondary" data-action="operation-preset" data-preset="feed">${icon('sparkles')}<span>推荐流</span></button>
          <button class="button small secondary" data-action="operation-preset" data-preset="me">${icon('users')}<span>当前用户</span></button>
          <button class="button small secondary" data-action="operation-preset" data-preset="creator">${icon('edit')}<span>创作者笔记</span></button>
          <button class="button small secondary" data-action="operation-preset" data-preset="chats">${icon('message')}<span>聊天列表</span></button>
        </div>
        <div class="operation-form">
          <div class="operation-fields">
            <label><span>操作</span><select id="operation-select" data-operation-field="operation">${options}</select></label>
            <label><span>位置参数 JSON</span><textarea id="operation-args" data-operation-field="args" rows="5">${escapeHtml(state.operationDraft.args)}</textarea></label>
            <label><span>关键字参数 JSON</span><textarea id="operation-kwargs" data-operation-field="kwargs" rows="7">${escapeHtml(state.operationDraft.kwargs)}</textarea></label>
            <div class="check-row">
              <label><input type="checkbox" id="operation-confirm" data-operation-field="confirm" ${state.operationDraft.confirm ? 'checked' : ''} /><span>确认写操作</span></label>
              <label><input type="checkbox" id="operation-deliver" data-operation-field="deliver" ${state.operationDraft.deliver ? 'checked' : ''} /><span>结果同步到飞书</span></label>
            </div>
            ${selected?.mutating ? '<div class="error-banner">该方法会修改账号状态，必须勾选“确认写操作”。</div>' : ''}
            <button class="button primary" data-action="execute-operation">${icon('play')}<span>执行操作</span></button>
            <div class="namespace-summary">${namespaceSummary}</div>
          </div>
          <pre class="result-view">${escapeHtml(result)}</pre>
        </div>
      </div>
    </section>`;
}

function renderFeishu() {
  const configured = Boolean(state.dashboard?.feishu_webhook_configured);
  const counts = state.deliveries.reduce((result, item) => {
    result[item.status] = (result[item.status] || 0) + 1;
    return result;
  }, {});
  const rows = state.deliveries.map((delivery) => `<tr><td><span class="row-title mono">${escapeHtml(delivery.job_id)}</span><span class="row-subtitle">${escapeHtml(delivery.job_type || 'summary')}</span></td><td>${pill(delivery.status)}</td><td class="number">${delivery.delivered_parts || 0}</td><td class="number">${delivery.attempts || 0}</td><td>${formatTime(delivery.updated)}</td><td class="ellipsis" title="${escapeHtml(delivery.last_error || '')}">${escapeHtml(delivery.last_error || '-')}</td></tr>`).join('');
  return `
    <div class="metric-grid">
      ${metric('Webhook', configured ? '已连接' : '未配置', '小红书消息同步群', 'send')}
      ${metric('待发送', (counts.ready || 0) + (counts.pending || 0), `${counts.processing || 0} 个处理中`, 'clock')}
      ${metric('已投递', counts.sent || 0, '按消息分片记录回执', 'check')}
      ${metric('失败', counts.failed || 0, '保留错误码供复核', 'alert')}
    </div>
    <div class="compose-layout">
      <section class="panel">
        ${sectionHeader('发送消息', '投递到 XHS 专属飞书群')}
        <div class="panel-body compose-form">
          <label><span>消息正文</span><textarea id="feishu-message" rows="10" maxlength="12000" placeholder="输入需要同步到群内的内容">${escapeHtml(state.feishuDraft)}</textarea></label>
          <div class="compose-meta"><span>最多 12,000 字，服务端按飞书限制自动分片</span><span id="feishu-char-count">${state.feishuDraft.length} / 12000</span></div>
          <button class="button primary" data-action="send-feishu" ${configured ? '' : 'disabled'}>${icon('send')}<span>发送到飞书</span></button>
        </div>
      </section>
      <section class="panel">
        ${sectionHeader('投递台账', `${state.deliveries.length} 个最近任务`)}
        <div class="panel-body flush">${rows ? `<div class="table-wrap"><table><thead><tr><th>任务</th><th>状态</th><th>分片</th><th>尝试</th><th>更新</th><th>错误</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState('暂无投递任务', '摘要或人工消息进入队列后会显示在这里。')}</div>
      </section>
    </div>`;
}

function render() {
  const meta = pageMeta[state.activeView];
  document.querySelector('#page-title').textContent = meta[0];
  document.querySelector('#page-subtitle').textContent = meta[1];
  document.querySelectorAll('.nav-item').forEach((node) => {
    const active = node.dataset.view === state.activeView;
    node.classList.toggle('active', active);
    node.setAttribute('aria-current', active ? 'page' : 'false');
  });
  if (state.loading && !state.dashboard) {
    content.innerHTML = emptyState('正在读取运行状态', '正在连接 47 Edge 的 XHS 服务。');
    return;
  }
  const error = state.error ? `<div class="error-banner">数据读取失败：${escapeHtml(state.error)}</div>` : '';
  const views = { overview: renderOverview, recommendations: renderRecommendations, single: renderSingleNotes, topics: renderTopics, following: renderFollowing, capabilities: renderCapabilities, feishu: renderFeishu };
  content.innerHTML = error + views[state.activeView]();
}

function openTopicDialog(slug = '') {
  const form = document.querySelector('#topic-form');
  form.reset();
  const topic = (state.dashboard?.topics || []).find((item) => item.slug === slug);
  document.querySelector('#topic-dialog-title').textContent = topic ? '编辑主题' : '新增主题';
  if (topic) {
    const policy = topic.policy || {};
    form.elements.slug.value = topic.slug;
    form.elements.slug.readOnly = true;
    form.elements.name.value = topic.name;
    form.elements.description.value = policy.description || topic.description || '';
    form.elements.include_keywords.value = (policy.include_keywords || []).join(', ');
    form.elements.exclude_keywords.value = (policy.exclude_keywords || []).join(', ');
    form.elements.threshold.value = policy.threshold ?? 0.65;
    form.elements.enabled.checked = Boolean(topic.enabled);
  } else {
    form.elements.slug.readOnly = false;
    form.elements.threshold.value = '0.65';
    form.elements.enabled.checked = true;
  }
  topicDialog.showModal();
}

async function runAction(button, label, task) {
  if (button) button.disabled = true;
  try {
    await task();
    toast(label);
    await refreshAll({ quiet: true });
  } catch (error) {
    toast(`${label}失败：${error.message || error}`, 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

document.addEventListener('click', async (event) => {
  const nav = event.target.closest('[data-view]');
  if (nav) {
    state.activeView = nav.dataset.view;
    render();
    if (state.activeView === 'recommendations' && state.selectedRunId) await loadRecommendationItems(state.selectedRunId);
    return;
  }
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const action = button.dataset.action;
  if (action === 'run-recommendations') await runAction(button, '推荐扫描已启动', async () => {
    const accepted = await post('/v1/dashboard/recommendations/run', { limit: 50, trigger: 'dashboard' });
    if (accepted.run_id) state.selectedRunId = accepted.run_id;
  });
  if (action === 'retry-recommendation') await runAction(button, 'AI 筛选已重新排队', () => post('/v1/dashboard/recommendations/retry', { run_id: button.dataset.runId }));
  if (action === 'analyze-single') {
    if (!state.singleDraft.trim()) { toast('请输入小红书文章链接', 'error'); return; }
    await runAction(button, '文章已进入 AI 解析队列', async () => {
      await post('/v1/dashboard/single-notes', { url: state.singleDraft.trim(), deliver_to_feishu: state.singleDeliver });
      state.singleDraft = '';
    });
  }
  if (action === 'select-run') await loadRecommendationItems(button.dataset.runId);
  if (action === 'new-topic') openTopicDialog();
  if (action === 'edit-topic') openTopicDialog(button.dataset.slug);
  if (action === 'toggle-topic') await runAction(button, '主题状态已更新', () => post('/v1/dashboard/topics', { action: button.dataset.enabled === '1' ? 'disable' : 'enable', slug: button.dataset.slug }));
  if (action === 'following-tab') { state.followingTab = button.dataset.tab; render(); }
  if (action === 'new-watch') { document.querySelector('#watch-form').reset(); watchDialog.showModal(); }
  if (action === 'run-watch') await runAction(button, '监控名单扫描已启动', () => post('/v1/dashboard/watch/run', { trigger: 'dashboard' }));
  if (action === 'sync-following') await runAction(button, '关注同步已启动', () => post('/v1/dashboard/following/sync', { trigger: 'dashboard' }));
  if (action === 'screen-following') await runAction(button, '关注筛选已启动', () => post('/v1/dashboard/following/screen', { limit: 50, trigger: 'dashboard' }));
  if (action === 'remove-watch' && window.confirm('从监控名单移除此账号？')) await runAction(button, '账号已移除', () => post('/v1/dashboard/watch-users', { action: 'remove', user_id: button.dataset.userId }));
  if (action === 'approve-candidate') await runAction(button, '账号已加入监控', () => post('/v1/dashboard/following/candidates', { action: 'approve', user_id: button.dataset.userId }));
  if (action === 'reject-candidate' && window.confirm('排除此关注候选？')) await runAction(button, '候选已排除', () => post('/v1/dashboard/following/candidates', { action: 'reject', user_id: button.dataset.userId }));
  if (action === 'operation-preset') {
    state.operationDraft = { ...state.operationDraft, ...operationPresets[button.dataset.preset], confirm: false };
    state.operationResult = null;
    render();
  }
  if (action === 'execute-operation') {
    await runAction(button, '操作已完成', async () => {
      let args;
      let kwargs;
      try {
        args = JSON.parse(state.operationDraft.args || '[]');
        kwargs = JSON.parse(state.operationDraft.kwargs || '{}');
      } catch {
        throw new Error('参数必须是有效 JSON');
      }
      if (!Array.isArray(args) || !kwargs || Array.isArray(kwargs) || typeof kwargs !== 'object') throw new Error('args 必须是数组，kwargs 必须是对象');
      if (state.operationDraft.confirm) kwargs.confirm = true;
      const result = await post('/v1/dashboard/operation', {
        operation: state.operationDraft.operation,
        args,
        kwargs,
        deliver_to_feishu: state.operationDraft.deliver,
        request_id: crypto.randomUUID(),
      });
      state.operationResult = result;
    });
  }
  if (action === 'send-feishu') {
    if (!state.feishuDraft.trim()) { toast('请输入消息正文', 'error'); return; }
    await runAction(button, '消息已进入飞书投递队列', async () => {
      await post('/v1/dashboard/feishu/messages', { text: state.feishuDraft.trim(), request_id: crypto.randomUUID() });
      state.feishuDraft = '';
    });
  }
});

document.addEventListener('input', (event) => {
  const field = event.target.dataset?.operationField;
  if (field) state.operationDraft[field] = event.target.type === 'checkbox' ? event.target.checked : event.target.value;
  if (event.target.id === 'feishu-message') {
    state.feishuDraft = event.target.value;
    const count = document.querySelector('#feishu-char-count');
    if (count) count.textContent = `${state.feishuDraft.length} / 12000`;
  }
  if (event.target.id === 'single-note-url') state.singleDraft = event.target.value;
});

document.addEventListener('change', (event) => {
  const field = event.target.dataset?.operationField;
  if (field) state.operationDraft[field] = event.target.type === 'checkbox' ? event.target.checked : event.target.value;
  if (field === 'operation') render();
  if (event.target.id === 'single-note-deliver') state.singleDeliver = event.target.checked;
});

refreshButton.addEventListener('click', () => refreshAll());

document.querySelector('#topic-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (event.submitter?.value === 'cancel') { topicDialog.close(); return; }
  const form = event.currentTarget;
  const data = new FormData(form);
  const split = (value) => String(value || '').split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);
  await runAction(event.submitter, '主题已保存', async () => {
    await post('/v1/dashboard/topics', {
      action: 'upsert', slug: data.get('slug'), name: data.get('name'), description: data.get('description'),
      include_keywords: split(data.get('include_keywords')), exclude_keywords: split(data.get('exclude_keywords')),
      threshold: Number(data.get('threshold')), enabled: data.get('enabled') === 'on',
    });
    topicDialog.close();
  });
});

document.querySelector('#watch-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (event.submitter?.value === 'cancel') { watchDialog.close(); return; }
  const data = new FormData(event.currentTarget);
  await runAction(event.submitter, '账号已加入监控', async () => {
    await post('/v1/dashboard/watch-users', { action: 'add', user_id: data.get('user_id'), label: data.get('label') });
    watchDialog.close();
  });
});

hydrateIcons();
render();
refreshAll();
setInterval(() => refreshAll({ quiet: true }), 30000);
