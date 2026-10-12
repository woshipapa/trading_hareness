import assert from 'node:assert/strict';
import test from 'node:test';
import { createGroupSync, describeGroups, historyProgress, membershipTransition, nextHistoryPage, routesToArchive } from './group-sync.mjs';

const NOW = Date.parse('2026-10-12T07:00:00Z');

test('a route is marked as left only on the second negative check in a row', () => {
	const first = membershipTransition({ membership: 'member', miss_count: 0 }, { member: false }, new Date(NOW));
	assert.deepEqual(first, { membership: 'member', miss_count: 1, left_at: null, changed: null });
	const second = membershipTransition(first, { member: false }, new Date(NOW));
	assert.equal(second.membership, 'left');
	assert.equal(second.changed, 'left');
	assert.equal(second.left_at.getTime(), NOW);
	// already known as left: the first date is kept and nothing is announced again
	const third = membershipTransition(second, { member: false }, new Date(NOW + 60_000));
	assert.equal(third.left_at.getTime(), NOW);
	assert.equal(third.changed, null);
});

test('an unknown answer changes nothing, a positive one clears a pending miss and recovers a left group', () => {
	const pending = { membership: 'member', miss_count: 1, left_at: null };
	assert.deepEqual(membershipTransition(pending, { member: null }, new Date(NOW)), { ...pending, changed: null });
	assert.deepEqual(membershipTransition(pending, undefined, new Date(NOW)), { ...pending, changed: null });
	assert.deepEqual(membershipTransition(pending, { member: true }, new Date(NOW)), { membership: 'member', miss_count: 0, left_at: null, changed: null });
	assert.equal(membershipTransition({ membership: 'left', miss_count: 5, left_at: new Date(NOW) }, { member: true }, new Date(NOW)).changed, 'rejoined');
	assert.equal(membershipTransition({ membership: 'member', miss_count: 1 }, { member: false, dissolved: true }, new Date(NOW)).membership, 'dissolved');
	// no stored row yet: treated as a member with no misses
	assert.equal(membershipTransition(undefined, { member: false }, new Date(NOW)).miss_count, 1);
});

test('history is read forward from the first visible position, one page at a time', () => {
	assert.deepEqual(nextHistoryPage({ history_first_position: 1, history_last_position: 120, history_synced_position: null }), { start: 1, end: 50 });
	assert.deepEqual(nextHistoryPage({ history_first_position: 1, history_last_position: 120, history_synced_position: 100 }), { start: 101, end: 120 });
	assert.equal(nextHistoryPage({ history_first_position: 1, history_last_position: 120, history_synced_position: 120 }), null);
	// a group that hides earlier history from new members starts at its first visible position
	assert.deepEqual(nextHistoryPage({ history_first_position: 4000, history_last_position: 4010, history_synced_position: null }), { start: 4000, end: 4010 });
	assert.equal(nextHistoryPage({ history_last_position: null }), null);
	assert.equal(nextHistoryPage(undefined), null);
});

test('progress reports what is stored against what the group has', () => {
	assert.equal(historyProgress(undefined).status, 'idle');
	assert.deepEqual(historyProgress({ history_first_position: 1, history_last_position: 200, history_synced_position: 50, history_status: 'running', history_message_count: 41 }),
		{ status: 'running', synced_position: 50, last_position: 200, messages: 41, percent: 25, error: null, synced_at: null });
	assert.equal(historyProgress({ history_first_position: 1, history_last_position: 200, history_synced_position: 200, history_status: 'complete' }).status, 'complete');
	// complete yesterday, new messages since: running again without anyone resetting the status
	assert.equal(historyProgress({ history_first_position: 1, history_last_position: 230, history_synced_position: 200, history_status: 'complete' }).status, 'running');
	assert.equal(historyProgress({ history_first_position: 1, history_last_position: 200, history_synced_position: 50, history_status: 'failed', history_error: 'timeout' }).error, 'timeout');
});

test('the group list marks newly joined groups and still shows a bound group the account has left', () => {
	const groups = describeGroups({
		now: NOW,
		groups: [{ chat_id: '7001', name: '小杰夜报B', member_count: 4 }, { chat_id: '7002', name: '新进的群', member_count: 9 }, { chat_id: '7003', name: '早就在的群' }],
		bound: [{ chat_id: '7001', source_key: 'relay_x', chat_name: '小杰夜报B' }, { chat_id: '7009', source_key: 'relay_gone', chat_name: '已退的群' }],
		states: [
			{ chat_id: '7001', seeded: true, membership: 'member', history_first_position: 1, history_last_position: 200, history_synced_position: 200, history_status: 'complete', history_message_count: 181 },
			{ chat_id: '7002', seeded: false, first_seen_at: new Date(NOW - 3600_000) },
			{ chat_id: '7003', seeded: true, first_seen_at: new Date(NOW - 3600_000) },
			{ chat_id: '7009', seeded: true, membership: 'left', left_at: new Date(NOW - 86_400_000), chat_name: '已退的群' },
		],
		routes: [{ key: 'relay_x', tag: 'xiaojie' }, { key: 'relay_gone', tag: 'old' }],
	});
	assert.deepEqual(groups.map((group) => [group.name, group.is_new, group.source_key, group.route_tag, group.membership]), [
		['新进的群', true, null, null, 'member'],
		['小杰夜报B', false, 'relay_x', 'xiaojie', 'member'],
		['已退的群', false, 'relay_gone', 'old', 'left'],
		['早就在的群', false, null, null, 'member'],
	]);
	assert.equal(groups[1].history.messages, 181);
	assert.equal(groups[0].history, null);
	// a group first seen long ago is no longer "new"
	const old = describeGroups({ now: NOW, groups: [{ chat_id: '7002', name: 'x' }], states: [{ chat_id: '7002', seeded: false, first_seen_at: new Date(NOW - 30 * 86_400_000) }] });
	assert.equal(old[0].is_new, false);
});

function fakeLedger() {
	const states = new Map();
	const history = new Map();
	return {
		states, history,
		async recordJoinedGroups(groups) { for (const group of groups) if (!states.has(group.chat_id)) states.set(group.chat_id, { chat_id: group.chat_id, chat_name: group.name, seeded: states.size === 0, membership: 'member', miss_count: 0, history_status: 'idle' }); },
		async groupChatStates() { return [...states.values()].map((state) => ({ ...state })); },
		async saveGroupChatState(chatId, patch) { states.set(chatId, { chat_id: chatId, membership: 'member', miss_count: 0, history_status: 'idle', ...states.get(chatId), ...patch }); },
		async upsertGroupHistory(chatId, rows) { const stored = history.get(chatId) ?? new Map(); for (const row of rows) stored.set(row.source_message_id, row); history.set(chatId, stored); return rows.length; },
		async groupHistoryCount(chatId) { return history.get(chatId)?.size ?? 0; },
	};
}

test('a bound group has its whole history read, a few pages per pass, and only bound groups are read', async () => {
	const ledger = fakeLedger();
	const pages = [];
	let clock = NOW;
	const sync = createGroupSync({
		ledger, now: () => clock, sleep: async () => {}, pagesPerTick: 2, directoryIntervalMs: 300_000,
		toRow: (payload) => payload.position % 10 === 0 ? null : { source_message_id: String(payload.msg_id), position: payload.position, source_create_time: 1, message: {} },
		bridge: {
			joined: async () => ({ groups: [{ chat_id: '7001', name: '小杰夜报B' }, { chat_id: '7002', name: '没绑定的群' }], bound: [{ chat_id: '7001', source_key: 'relay_x', chat_name: '小杰夜报B' }] }),
			membership: async (ids) => ({ chats: Object.fromEntries(ids.map((id) => [id, { member: true, name: '小杰夜报B', first_position: 1, last_position: 120 }])) }),
			page: async (chatId, start, end) => { pages.push([chatId, start, end]); return { messages: Array.from({ length: end - start + 1 }, (_, index) => ({ msg_id: start + index, position: start + index })) }; },
		},
	});
	assert.deepEqual(await sync.tick(), { directory_refreshed: true });
	assert.deepEqual(pages, [['7001', 1, 50], ['7001', 51, 100]]);
	assert.equal(ledger.states.get('7001').history_status, 'running');
	clock += 30_000;
	assert.deepEqual(await sync.tick(), { directory_refreshed: false });
	assert.deepEqual(pages.at(-1), ['7001', 101, 120]);
	const state = ledger.states.get('7001');
	assert.equal(state.history_status, 'complete');
	assert.equal(state.history_synced_position, 120);
	assert.equal(state.history_message_count, 108); // 120 positions, 12 of them not content
	assert.equal(ledger.history.has('7002'), false);
	clock += 30_000;
	await sync.tick();
	assert.equal(pages.length, 3); // nothing new: no further reads
});

test('leaving a group stops its history reads; a failed read is retried on the next directory pass, not every pass', async () => {
	const ledger = fakeLedger();
	let clock = NOW;
	let member = true;
	let failing = true;
	const pages = [];
	const logs = [];
	const sync = createGroupSync({
		ledger, now: () => clock, sleep: async () => {}, pagesPerTick: 4, directoryIntervalMs: 300_000, log: (line) => logs.push(line), toRow: (payload) => ({ source_message_id: String(payload.msg_id), position: payload.position, source_create_time: 1, message: {} }),
		bridge: {
			joined: async () => ({ groups: member ? [{ chat_id: '7001', name: '甲群' }] : [], bound: [{ chat_id: '7001', source_key: 'relay_x', chat_name: '甲群' }] }),
			membership: async () => ({ chats: { 7001: member ? { member: true, name: '甲群', first_position: 1, last_position: 60 } : { member: false } } }),
			page: async (chatId, start, end) => { pages.push([start, end]); if (failing) throw new Error('gateway timeout'); return { messages: [{ msg_id: start, position: start }] }; },
		},
	});
	await sync.tick();
	assert.equal(ledger.states.get('7001').history_status, 'failed');
	assert.equal(ledger.states.get('7001').history_error, 'gateway timeout');
	clock += 30_000; await sync.tick();
	assert.equal(pages.length, 1); // not retried between directory passes
	failing = false; clock += 300_000; await sync.tick();
	assert.equal(ledger.states.get('7001').history_status, 'complete');
	member = false;
	clock += 300_000; await sync.tick();
	assert.equal(ledger.states.get('7001').membership, 'member'); // first negative answer: not yet
	clock += 300_000; await sync.tick();
	assert.equal(ledger.states.get('7001').membership, 'left');
	assert.ok(logs.some((line) => /7001.*left/.test(line)));
	const reads = pages.length;
	ledger.states.get('7001').history_last_position = 999; // even if the range looked open
	clock += 300_000; await sync.tick();
	assert.equal(pages.length, reads);
	member = true; clock += 300_000; await sync.tick();
	assert.equal(ledger.states.get('7001').membership, 'member');
	assert.ok(logs.some((line) => /rejoined/.test(line)));
});

test('an unreachable bridge is reported and changes no stored state', async () => {
	const ledger = fakeLedger();
	ledger.states.set('7001', { chat_id: '7001', membership: 'member', miss_count: 0 });
	const sync = createGroupSync({ ledger, now: () => NOW, sleep: async () => {}, toRow: () => null, bridge: { joined: async () => { throw new Error('bridge HTTP 503'); }, membership: async () => ({}), page: async () => ({}) } });
	assert.deepEqual(await sync.tick(), { error: 'bridge HTTP 503' });
	assert.equal(sync.status().last_error, 'bridge HTTP 503');
	assert.deepEqual(ledger.states.get('7001'), { chat_id: '7001', membership: 'member', miss_count: 0 });
});

test('the group with the least left to read is read first', async () => {
	const ledger = fakeLedger();
	const pages = [];
	const sync = createGroupSync({
		ledger, now: () => NOW, sleep: async () => {}, pagesPerTick: 3, toRow: (payload) => ({ source_message_id: String(payload.msg_id), position: payload.position, source_create_time: 1, message: {} }),
		bridge: {
			joined: async () => ({ groups: [{ chat_id: '7001', name: 'A 大群' }, { chat_id: '7002', name: 'B 新绑定的小群' }], bound: [{ chat_id: '7001', source_key: 'a' }, { chat_id: '7002', source_key: 'b' }] }),
			membership: async () => ({ chats: { 7001: { member: true, first_position: 1, last_position: 5000 }, 7002: { member: true, first_position: 1, last_position: 80 } } }),
			page: async (chatId, start, end) => { pages.push([chatId, start, end]); return { messages: [{ msg_id: `${chatId}-${start}`, position: start }] }; },
		},
	});
	await sync.tick();
	assert.deepEqual(pages, [['7002', 1, 50], ['7002', 51, 80], ['7001', 1, 50]]);
	assert.equal(ledger.states.get('7002').history_status, 'complete');
});

test('a route is archived only when the account is confirmed out of every group that feeds it', () => {
	assert.deepEqual(routesToArchive([
		{ chat_id: '1', source_key: 'gone', membership: 'left', miss_count: 3 },
		{ chat_id: '2', source_key: 'just_marked', membership: 'left', miss_count: 2 },
		{ chat_id: '3', source_key: 'two_groups', membership: 'left', miss_count: 9 },
		{ chat_id: '4', source_key: 'two_groups', membership: 'member', miss_count: 0 },
		{ chat_id: '5', source_key: 'dissolved', membership: 'dissolved', miss_count: 4 },
		{ chat_id: '6', source_key: 'fine', membership: 'member', miss_count: 0 },
		{ chat_id: '7', membership: 'left', miss_count: 9 },
	]), ['gone', 'dissolved']);
	assert.deepEqual(routesToArchive([]), []);
});

test('leaving a group archives its route after one more confirmed check; coming back restores it', async () => {
	const ledger = fakeLedger();
	let clock = NOW;
	const archivedRoutes = new Map();
	const calls = [];
	const logs = [];
	let answer = { member: false };
	const sync = createGroupSync({
		ledger, now: () => clock, sleep: async () => {}, directoryIntervalMs: 300_000, log: (line) => logs.push(line), toRow: () => null,
		archiveRoute: async (key, detail) => { calls.push(['archive', key, detail]); archivedRoutes.set(key, detail.chatIds); return { key, tag: 'diaoyan', chat_name: '调研纪要' }; },
		restoreRoute: async (key, chatId) => { calls.push(['restore', key, chatId]); archivedRoutes.delete(key); return { key }; },
		bridge: {
			joined: async () => archivedRoutes.has('relay_dy')
				? { groups: [], bound: [], archived: [{ chat_id: '7001', source_key: 'relay_dy', chat_name: '调研纪要' }] }
				: { groups: [], bound: [{ chat_id: '7001', source_key: 'relay_dy', chat_name: '调研纪要' }], archived: [] },
			membership: async () => ({ chats: { 7001: answer } }), page: async () => ({ messages: [] }),
		},
	});
	await sync.tick();                    // 1st negative
	clock += 300_000; await sync.tick();  // 2nd: marked left, route still running
	assert.equal(ledger.states.get('7001').membership, 'left');
	assert.deepEqual(calls, []);
	answer = { member: null };            // an unknown answer does not move it closer
	clock += 300_000; await sync.tick();
	assert.deepEqual(calls, []);
	answer = { member: false };
	clock += 300_000; await sync.tick();  // 3rd negative: archived, with the group ids it had
	assert.deepEqual(calls, [['archive', 'relay_dy', { reason: 'left', chatIds: ['7001'] }]]);
	assert.ok(logs.some((line) => /已自动停用退群路由 source_key=relay_dy tag=diaoyan/.test(line)));
	assert.equal(sync.status().bound_chats, 0);
	clock += 300_000; await sync.tick();
	assert.equal(calls.length, 1);        // still out: nothing more happens, and it is not archived twice
	assert.equal(ledger.states.get('7001').membership, 'left');
	answer = { member: true, first_position: 1, last_position: 40 };
	clock += 300_000; await sync.tick();  // back in the group: the route resumes
	assert.deepEqual(calls.at(-1), ['restore', 'relay_dy', '7001']);
	assert.deepEqual([ledger.states.get('7001').membership, ledger.states.get('7001').miss_count, ledger.states.get('7001').left_at], ['member', 0, null]);
	assert.ok(logs.some((line) => /自动恢复路由 source_key=relay_dy/.test(line)));
	assert.equal(sync.status().bound_chats, 1);
});

test('a group that comes back before the route is archived keeps its route; without the callbacks nothing is archived', async () => {
	const ledger = fakeLedger();
	let clock = NOW;
	const archived = [];
	let answer = { member: false };
	const bridge = { joined: async () => ({ groups: [], bound: [{ chat_id: '7001', source_key: 'relay_x' }] }), membership: async () => ({ chats: { 7001: answer } }), page: async () => ({ messages: [] }) };
	const sync = createGroupSync({ ledger, now: () => clock, sleep: async () => {}, directoryIntervalMs: 300_000, toRow: () => null, archiveRoute: async (key) => { archived.push(key); return { key }; }, bridge });
	await sync.tick(); clock += 300_000; await sync.tick();
	answer = { member: true, first_position: 1, last_position: 0 };
	clock += 300_000; await sync.tick();
	answer = { member: false };
	clock += 300_000; await sync.tick(); clock += 300_000; await sync.tick();
	assert.deepEqual(archived, []); // the count started over when the account was back
	const passive = createGroupSync({ ledger: fakeLedger(), now: () => clock, sleep: async () => {}, directoryIntervalMs: 0, toRow: () => null, bridge });
	for (let pass = 0; pass < 5; pass += 1) await passive.tick();
	assert.deepEqual(archived, []);
});

test('the group of an archived route is not offered for a second route', () => {
	const groups = describeGroups({
		now: NOW, groups: [{ chat_id: '7001', name: '调研纪要' }, { chat_id: '7002', name: '别的群' }],
		archived: [{ chat_id: '7001', source_key: 'relay_dy' }, { chat_id: '7009', source_key: 'relay_old' }],
		states: [{ chat_id: '7001', seeded: true, membership: 'left' }], routes: [{ key: 'relay_dy', tag: 'diaoyan' }],
	});
	// back in the directory, still tied to its archived route; a gone group of an archived route is not listed here at all
	assert.deepEqual(groups.map((group) => [group.name, group.source_key, group.route_tag, group.route_archived, group.is_new]), [['别的群', null, null, false, false], ['调研纪要', 'relay_dy', 'diaoyan', true, false]]);
});
