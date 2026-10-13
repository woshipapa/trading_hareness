import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { defineComponent, h, inject, nextTick, provide, ref, unref } from 'vue';
import TargetMembersPanel from './TargetMembersPanel.vue';
import { targetGroupsContextKey } from '../../dashboard-context';

const jsonResponse = (value: unknown) => new Response(JSON.stringify(value), {
  headers: { 'content-type': 'application/json' },
});

afterEach(() => vi.unstubAllGlobals());

const tableRowKey = Symbol('table-row');
const rowProvider = defineComponent({
  props: { row: { type: Object, required: true } },
  setup(props, { slots }) {
    provide(tableRowKey, props.row);
    return () => slots.default?.();
  },
});
const tableStub = defineComponent({
  props: { data: { type: Array, default: () => [] } },
  setup(props, { slots }) {
    return () => h('div', { class: 'el-table__body-wrapper' }, (unref(props.data) as Record<string, unknown>[]).map((row) => h(rowProvider, { row }, { default: () => slots.default?.() })));
  },
});
const columnStub = defineComponent({
  setup(_props, { slots }) {
    return () => h('div', slots.default?.({ row: inject(tableRowKey) }));
  },
});
const simpleStub = (tag: string) => defineComponent({ setup(_props, { slots }) { return () => h(tag, slots.default?.()); } });
const buttonStub = defineComponent({ setup(_props, { slots, attrs }) { return () => h('button', attrs, slots.default?.()); } });

describe('TargetMembersPanel', () => {
  it('labels bots and the account, and only offers removal for people', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ status: 'ok', chat_id: '7001', members: [
      { user_id: '100', name: '本账号', joined_at: 1, is_bot: false, is_self: true },
      { user_id: 'bot', name: '转发机器人', joined_at: 2, is_bot: true, is_self: false },
      { user_id: '200', name: '成员甲', joined_at: 3, is_bot: false, is_self: false },
    ] })));
    const selectedTarget = ref({
      chat_id: 'target', name: '目标群', keyword: '转发', origin: 'dashboard' as const, usable: true,
      websocket_chat_id: '7001', bot_id: 'bot', bot_name: '转发机器人', owned: true,
    });
    const wrapper = mount(TargetMembersPanel, {
      global: {
        provide: { [targetGroupsContextKey]: { selectedTarget } },
        stubs: {
          'el-card': simpleStub('section'), 'el-alert': simpleStub('div'), 'el-text': simpleStub('span'),
          'el-tabs': simpleStub('div'), 'el-tab-pane': simpleStub('div'), 'el-tag': simpleStub('span'),
          'el-table': tableStub, 'el-table-column': columnStub, 'el-button': buttonStub,
        },
      },
    });
    await nextTick();
    await flushPromises();

    expect(wrapper.text()).toContain('机器人');
    expect(wrapper.text()).toContain('本账号');
    expect(wrapper.findAll('.el-table__body-wrapper button')).toHaveLength(1);
    expect(wrapper.find('.el-table__body-wrapper button').text()).toBe('移除');
  });
});
