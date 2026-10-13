import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { defineComponent, h, provide, ref } from 'vue';
import ElementPlus from 'element-plus';
import TargetRegistryPanel from './TargetRegistryPanel.vue';
import MembershipPanel from './MembershipPanel.vue';
import TargetGroupsView from '../../views/TargetGroupsView.vue';
import { targetGroupsContextKey } from '../../dashboard-context';
import type { TargetRegistryRow } from '../../api/target-registry';

const jsonResponse = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } });
const target: TargetRegistryRow = { chat_id: 'oc_summary', name: '分析师发送汇总群', keyword: '汇总', origin: 'env', usable: true, managed: false, websocket_chat_id: '7669744189332065542', bot_id: '1', bot_name: '汇总bot', owned: true };

afterEach(() => vi.unstubAllGlobals());

// The real Element Plus components are mounted on purpose: a stub that tolerates a ref where an
// array or a boolean is expected hides exactly the fault this guards against (2026-10-13: the
// registry table stayed empty and its button spun forever because the template read refs through
// a plain object without unwrapping them).
function mountWithContext(component: unknown, selected: TargetRegistryRow | null) {
  const host = defineComponent({
    setup() {
      provide(targetGroupsContextKey, { selectedTarget: ref(selected) });
      return () => h(component as never);
    },
  });
  return mount(host, { global: { plugins: [ElementPlus] }, attachTo: document.body });
}

describe('target group panels render what their composables hold', () => {
  it('shows the registry rows, no error and an idle button once the list is loaded', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ targets: [target] })));
    const wrapper = mountWithContext(TargetRegistryPanel, null);
    await flushPromises();
    expect(wrapper.text()).toContain('分析师发送汇总群');
    expect(wrapper.text()).toContain('汇总bot');
    expect(wrapper.find('.el-alert').exists()).toBe(false);
    expect(wrapper.find('button').classes()).not.toContain('is-loading');
    wrapper.unmount();
  });

  it('lists a group the account owns beside the targets and offers to give it a bot', async () => {
    const owned: TargetRegistryRow = { chat_id: '7680753363566234587', name: '擒龙内参更新群1', keyword: null, origin: 'owned', usable: false, managed: false, websocket_chat_id: '7680753363566234587', bot_id: null, bot_name: null, owned: true };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ targets: [target, owned] })));
    const wrapper = mountWithContext(TargetRegistryPanel, null);
    await flushPromises();
    const rows = wrapper.findAll('.el-table__body tbody tr');
    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain('转发目标（环境变量）');
    expect(rows[0].text()).not.toContain('建机器人');
    expect(rows[1].text()).toContain('擒龙内参更新群1');
    expect(rows[1].text()).toContain('仅管理');
    expect(rows[1].text()).toContain('未接入');
    expect(rows[1].find('button').text()).toBe('建机器人');
    expect(rows[1].find('button').classes()).not.toContain('is-loading');
    wrapper.unmount();
  });

  it('shows the membership rows without a loading mask or an error once they are loaded', async () => {
    const membership = { target_chat_id: 'oc_summary', user_id: '200', display_name: '成员甲', started_on: '2026-09-01', term_months: 1, expires_on: '2026-10-01', note: null, reminded_on: null, left_at: null, status: '已到期' };
    const settings = { target_chat_id: 'oc_summary', enabled: false, default_term_months: 1, remind_text: '会员已到期，请联系群主续费', exempt_user_ids: [], last_review_on: null };
    vi.stubGlobal('fetch', vi.fn(async (url: string) => jsonResponse(String(url).includes('membership-settings') ? { settings } : { members: [membership], today: '2026-10-13', target_chat_id: 'oc_summary' })));
    const wrapper = mountWithContext(MembershipPanel, target);
    await flushPromises();
    expect(wrapper.text()).toContain('成员甲');
    expect(wrapper.find('.el-alert').exists()).toBe(false);
    wrapper.unmount();
  });

  it('mounts the whole page with every panel and hands no component a ref where a value belongs', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      const path = String(url);
      if (path.endsWith('/alerts/settings')) return jsonResponse({ settings: { enabled: false, chat_id: '', user_id: '', name: '' } });
      if (path.endsWith('/alerts/recipients')) return jsonResponse({ status: 'ok', recipients: [] });
      return jsonResponse({ targets: [target] });
    }));
    const wrapper = mount(TargetGroupsView, { global: { plugins: [ElementPlus] }, attachTo: document.body });
    await flushPromises();
    for (const title of ['我的群', '新建同步群', '成员管理', '会员期限与到期提醒', '送达核对与撤回同步', '告警加急']) expect(wrapper.text()).toContain(title);
    expect(wrapper.text()).toContain('分析师发送汇总群');
    // Vue reports a ref passed where an array, a string or a boolean is declared as an invalid prop.
    const invalid = warn.mock.calls.map((call) => String(call[0])).filter((text) => text.includes('Invalid prop') || text.includes('Unhandled error'));
    expect(invalid).toEqual([]);
    warn.mockRestore();
    wrapper.unmount();
  });
});
