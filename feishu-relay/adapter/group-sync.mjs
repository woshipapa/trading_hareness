// Keeps what the relay knows about the account's groups current without an
// operator: which groups it is in, whether it is still in each bound one, and
// a stored copy of every bound group's own history.  The gateway reads go
// through the bridge and the state lives in the ledger; both are passed in.

export const LEFT_AFTER_MISSES = 2;
export const NEW_GROUP_WINDOW_MS = 14 * 24 * 3600_000;

/** Membership after one check.
 *
 * `report.member` is true, false or null (the check itself failed).  A single
 * "not a member" answer is not acted on; the route is marked as left on the
 * second one in a row.  An unknown answer changes nothing, and a positive one
 * clears everything, so a rejoined group recovers by itself. */
export function membershipTransition(previous, report, now) {
	const before = { membership: previous?.membership ?? 'member', miss_count: Number(previous?.miss_count ?? 0), left_at: previous?.left_at ?? null };
	if (report?.member === true) return { membership: 'member', miss_count: 0, left_at: null, changed: before.membership === 'member' ? null : 'rejoined' };
	if (report?.member !== false) return { ...before, changed: null };
	const misses = before.miss_count + 1;
	if (misses < LEFT_AFTER_MISSES) return { membership: before.membership, miss_count: misses, left_at: before.left_at, changed: null };
	const membership = report.dissolved ? 'dissolved' : 'left';
	return { membership, miss_count: misses, left_at: before.left_at ?? now, changed: before.membership === 'member' ? membership : null };
}

/** The next positions to read for a chat, or null when its stored history is up to date. */
export function nextHistoryPage(state, pageSize = 50) {
	const last = Number(state?.history_last_position ?? 0);
	const first = Math.max(1, Number(state?.history_first_position ?? 1));
	const synced = state?.history_synced_position == null ? first - 1 : Number(state.history_synced_position);
	if (!Number.isFinite(last) || !(last > synced)) return null;
	const start = Math.max(first, synced + 1);
	return { start, end: Math.min(last, start + pageSize - 1) };
}

export function historyProgress(state) {
	if (!state || state.history_last_position == null) return { status: 'idle', synced_position: null, last_position: null, messages: 0, percent: null, error: null, synced_at: null };
	const first = Math.max(1, Number(state.history_first_position ?? 1));
	const last = Number(state.history_last_position);
	const synced = state.history_synced_position == null ? first - 1 : Number(state.history_synced_position);
	const span = Math.max(0, last - first + 1);
	const pending = nextHistoryPage(state) !== null;
	return {
		status: state.history_status === 'failed' ? 'failed' : pending ? 'running' : 'complete',
		synced_position: synced, last_position: last, messages: Number(state.history_message_count ?? 0),
		percent: span ? Math.min(100, Math.round((Math.max(0, synced - first + 1) / span) * 100)) : 100,
		error: state.history_error ?? null, synced_at: state.history_synced_at ?? null,
	};
}

/** The group list the dashboard shows: every joined group plus every bound one, each with its route and state. */
export function describeGroups({ groups = [], bound = [], states = [], routes = [], now = Date.now() }) {
	const stateById = new Map(states.map((state) => [String(state.chat_id), state]));
	const routeByKey = new Map(routes.map((route) => [route.key, route]));
	const boundById = new Map(bound.map((item) => [String(item.chat_id), item]));
	const listed = new Map();
	for (const group of groups) listed.set(String(group.chat_id), { chat_id: String(group.chat_id), name: group.name ?? '', member_count: group.member_count ?? null, in_directory: true });
	// A bound group the account has left is no longer in the directory, and is the one that most needs showing.
	for (const [chatId, item] of boundById) if (!listed.has(chatId)) listed.set(chatId, { chat_id: chatId, name: item.chat_name ?? '', member_count: null, in_directory: false });
	return [...listed.values()].map((group) => {
		const state = stateById.get(group.chat_id);
		const sourceKey = boundById.get(group.chat_id)?.source_key ?? null;
		const route = sourceKey ? routeByKey.get(sourceKey) : null;
		const firstSeen = state?.first_seen_at ? new Date(state.first_seen_at).getTime() : null;
		return {
			...group, name: group.name || state?.chat_name || '',
			source_key: sourceKey, route_tag: route?.tag ?? null, route_known: Boolean(route),
			membership: sourceKey ? state?.membership ?? 'member' : group.in_directory ? 'member' : 'left',
			left_at: state?.left_at ?? null,
			is_new: Boolean(!sourceKey && group.in_directory && state && state.seeded === false && firstSeen !== null && now - firstSeen < NEW_GROUP_WINDOW_MS),
			first_seen_at: state?.first_seen_at ?? null,
			history: sourceKey ? historyProgress(state) : null,
		};
	}).sort((a, b) => Number(b.is_new) - Number(a.is_new) || a.name.localeCompare(b.name, 'zh') || a.chat_id.localeCompare(b.chat_id));
}

export function createGroupSync({ ledger, bridge, toRow, log = () => {}, now = Date.now, sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
	directoryIntervalMs = 5 * 60_000, pagesPerTick = 6, pageDelayMs = 400 }) {
	let directoryAt = 0;
	let running = false;
	let forceDirectory = false;
	const runtime = { last_tick_at: null, last_directory_at: null, last_error: null, groups: [], bound: [] };

	async function refreshDirectory() {
		const joined = await bridge.joined();
		const groups = Array.isArray(joined?.groups) ? joined.groups : [];
		const bound = Array.isArray(joined?.bound) ? joined.bound : [];
		await ledger.recordJoinedGroups(groups);
		const reports = bound.length ? (await bridge.membership(bound.map((item) => String(item.chat_id))))?.chats ?? {} : {};
		const states = new Map((await ledger.groupChatStates()).map((state) => [String(state.chat_id), state]));
		for (const item of bound) {
			const chatId = String(item.chat_id);
			const report = reports[chatId];
			const previous = states.get(chatId);
			const next = membershipTransition(previous, report, new Date(now()));
			const patch = { membership: next.membership, miss_count: next.miss_count, left_at: next.left_at, checked_at: new Date(now()) };
			if (report?.member === true) {
				patch.history_first_position = report.first_position;
				patch.history_last_position = report.last_position;
				if (report.name) patch.chat_name = report.name;
			} else if (!previous?.chat_name && item.chat_name) patch.chat_name = item.chat_name;
			await ledger.saveGroupChatState(chatId, patch);
			if (next.changed) log(`群成员身份变化 chat_id=${chatId} name=${report?.name || item.chat_name || ''} ${next.changed}`);
		}
		runtime.groups = groups;
		runtime.bound = bound;
		runtime.last_directory_at = new Date(now()).toISOString();
	}

	async function syncHistory({ retryFailed }) {
		const boundIds = new Set(runtime.bound.map((item) => String(item.chat_id)));
		let budget = pagesPerTick;
		// Least left to read goes first, so a newly bound small group is complete
		// in one pass instead of waiting behind a large one.
		const remaining = (state) => { const page = nextHistoryPage(state); return page ? Number(state.history_last_position) - page.start + 1 : 0; };
		const states = (await ledger.groupChatStates()).sort((a, b) => remaining(a) - remaining(b));
		for (const state of states) {
			if (budget <= 0) break;
			const chatId = String(state.chat_id);
			if (!boundIds.has(chatId) || state.membership !== 'member') continue;
			if (state.history_status === 'failed' && !retryFailed) continue;
			while (budget > 0) {
				const page = nextHistoryPage(state);
				if (!page) {
					if (state.history_last_position != null && state.history_status !== 'complete') await ledger.saveGroupChatState(chatId, { history_status: 'complete', history_error: null });
					break;
				}
				budget -= 1;
				try {
					const result = await bridge.page(chatId, page.start, page.end);
					const rows = (Array.isArray(result?.messages) ? result.messages : []).map(toRow).filter(Boolean);
					await ledger.upsertGroupHistory(chatId, rows);
					state.history_synced_position = page.end;
					state.history_status = page.end >= Number(state.history_last_position) ? 'complete' : 'running';
					await ledger.saveGroupChatState(chatId, {
						history_status: state.history_status, history_synced_position: page.end, history_error: null,
						history_message_count: await ledger.groupHistoryCount(chatId), history_synced_at: new Date(now()),
					});
				} catch (error) {
					const message = (error instanceof Error ? error.message : String(error)).slice(0, 300);
					await ledger.saveGroupChatState(chatId, { history_status: 'failed', history_error: message });
					log(`群历史同步失败 chat_id=${chatId} positions=${page.start}-${page.end}：${message}`);
					break;
				}
				if (budget > 0 && pageDelayMs > 0) await sleep(pageDelayMs);
			}
		}
	}

	return {
		/** One pass: refresh the directory when due, then read a bounded number of history pages. */
		async tick() {
			if (running) return { skipped: true };
			running = true;
			try {
				const due = forceDirectory || now() - directoryAt >= directoryIntervalMs;
				if (due) {
					forceDirectory = false;
					await refreshDirectory();
					directoryAt = now();
				}
				await syncHistory({ retryFailed: due });
				runtime.last_error = null;
				return { directory_refreshed: due };
			} catch (error) {
				runtime.last_error = (error instanceof Error ? error.message : String(error)).slice(0, 300);
				return { error: runtime.last_error };
			} finally {
				runtime.last_tick_at = new Date(now()).toISOString();
				running = false;
			}
		},
		/** Ask for the directory to be re-read on the next pass (after a route was bound or removed). */
		requestRefresh() { forceDirectory = true; },
		status() { return { last_tick_at: runtime.last_tick_at, last_directory_at: runtime.last_directory_at, last_error: runtime.last_error, joined_groups: runtime.groups.length, bound_chats: runtime.bound.length }; },
		directory() { return { groups: runtime.groups, bound: runtime.bound }; },
	};
}
