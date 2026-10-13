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
    expect(rows[1].findAll('button').map((button) => button.text())).toEqual(['用已有机器人', '建机器人']);
    expect(rows[1].findAll('button').some((button) => button.classes().includes('is-loading'))).toBe(false);
    wrapper.unmount();
  });

  it('lets an owned group use a bot it already has: the bots are listed, one is taken, and the new row is selected', async () => {
    const owned: TargetRegistryRow = { chat_id: '7680753363566234587', name: '擒龙内参更新群1', keyword: null, origin: 'owned', usable: false, managed: false, websocket_chat_id: '7680753363566234587', bot_id: null, bot_name: null, owned: true };
    const connected: TargetRegistryRow = { ...owned, chat_id: 'wh_new', keyword: '', origin: 'dashboard', usable: true, bot_id: '41', bot_name: 'dragon' };
    let listed = [target, owned];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const path = String(url);
      if (path.endsWith('/bots')) return jsonResponse({ bots: [{ bot_id: '41', name: 'dragon', key_words: [], usable: true, reason: null }, { bot_id: '42', name: '签名机器人', key_words: ['x'], usable: false, reason: '这个机器人开了签名校验，中继不带签名发送' }] });
      if (path.endsWith('/bots/41/use') && init?.method === 'POST') { listed = [target, connected]; return jsonResponse({ target: connected }); }
      return jsonResponse({ targets: listed });
    });
    vi.stubGlobal('fetch', fetchMock);
    const selected = ref<TargetRegistryRow | null>(null);
    const host = defineComponent({ setup() { provide(targetGroupsContextKey, { selectedTarget: selected }); return () => h(TargetRegistryPanel); } });
    const wrapper = mount(host, { global: { plugins: [ElementPlus] }, attachTo: document.body });
    await flushPromises();
    await wrapper.findAll('.el-table__body tbody tr')[1].findAll('button')[0].trigger('click');
    await flushPromises();
    const dialog = document.body.querySelector('.el-dialog') as HTMLElement;
    expect(dialog.textContent).toContain('dragon');
    expect(dialog.textContent).toContain('签名校验');
    const use = [...dialog.querySelectorAll('button')].filter((button) => button.textContent?.includes('用它发提醒'));
    expect(use).toHaveLength(1);
    use[0].click();
    await flushPromises();
    expect(selected.value?.chat_id).toBe('wh_new');
    expect(wrapper.findAll('.el-table__body tbody tr')[1].text()).toContain('dragon');
    expect(wrapper.findAll('.el-table__body tbody tr')[1].text()).not.toContain('未接入');
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

  it('shows each member\'s alias, fills the edit form from it, and sends a typed expiry date', async () => {
    const membership = { target_chat_id: 'oc_summary', user_id: '200', display_name: '群昵称', alias: '阿明 到12月底 918三个月', started_on: '2026-09-01', term_months: 1, expires_on: '2026-10-01', expiry_fixed: false, note: null, reminded_on: null, left_at: null, status: '已到期',
      suggestions: [{ started_on: null, term_months: null, expires_on: '2026-12-31', label: '到 12-31', span: [3, 8] }, { started_on: '2026-09-18', term_months: 3, expires_on: null, label: '09-18 起 3 个月', span: [9, 15] }] };
    const settings = { target_chat_id: 'oc_summary', enabled: false, default_term_months: 1, remind_text: '会员已到期，请联系群主续费', exempt_user_ids: [], last_review_on: null };
    const sent: Array<Record<string, unknown>> = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === 'PUT') { sent.push(JSON.parse(String(init.body))); return jsonResponse({ member: membership }); }
      return jsonResponse(String(url).includes('membership-settings') ? { settings } : { members: [membership], today: '2026-10-13', target_chat_id: 'oc_summary' });
    }));
    const wrapper = mountWithContext(MembershipPanel, target);
    await flushPromises();
    expect(wrapper.find('.el-table__body tbody tr').text()).toContain('阿明 到12月底 918三个月');
    const edit = wrapper.findAll('.el-table__body tbody tr button').find((button) => button.text().includes('编辑'));
    await edit!.trigger('click');
    await flushPromises();
    const dialog = [...document.body.querySelectorAll('.el-dialog')].find((node) => node.textContent?.includes('编辑会员')) as HTMLElement;
    const click = async (label: string) => { ([...dialog.querySelectorAll('button')].find((button) => button.textContent?.trim() === label) as HTMLButtonElement).click(); await flushPromises(); };
    expect(dialog.textContent).toContain('按备注名填');
    await click('09-18 起 3 个月');
    await click('保存');
    expect(sent.at(-1)).toMatchObject({ started_on: '2026-09-18', term_months: 3, expires_on: null });
    await edit!.trigger('click');
    await flushPromises();
    const again = [...document.body.querySelectorAll('.el-dialog')].find((node) => node.textContent?.includes('编辑会员')) as HTMLElement;
    ([...again.querySelectorAll('button')].find((button) => button.textContent?.trim() === '到 12-31') as HTMLButtonElement).click();
    await flushPromises();
    ([...again.querySelectorAll('button')].find((button) => button.textContent?.trim() === '保存') as HTMLButtonElement).click();
    await flushPromises();
    expect(sent.at(-1)).toMatchObject({ started_on: '2026-09-01', expires_on: '2026-12-31' });
    // The alias is sent only when it was changed in the form.
    expect(sent.every((body) => !('alias' in body))).toBe(true);
    await edit!.trigger('click');
    await flushPromises();
    const third = [...document.body.querySelectorAll('.el-dialog')].find((node) => node.textContent?.includes('编辑会员')) as HTMLElement;
    ([...third.querySelectorAll('button')].find((button) => button.textContent?.trim() === '09-18 起 3 个月') as HTMLButtonElement).click();
    await flushPromises();
    const month = third.querySelector('.el-input-number input') as HTMLInputElement;
    month.value = '6';
    month.dispatchEvent(new Event('input'));
    month.dispatchEvent(new Event('change'));
    await flushPromises();
    ([...third.querySelectorAll('button')].find((button) => button.textContent?.trim() === '按下面的日期改写') as HTMLButtonElement).click();
    await flushPromises();
    expect((third.querySelector('input[placeholder="没有备注名"]') as HTMLInputElement).value).toBe('阿明 到12月底 918六个月');
    ([...third.querySelectorAll('button')].find((button) => button.textContent?.trim() === '保存') as HTMLButtonElement).click();
    await flushPromises();
    expect(sent.at(-1)).toMatchObject({ started_on: '2026-09-18', term_months: 6, alias: '阿明 到12月底 918六个月' });
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
