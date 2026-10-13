import { createHash } from 'node:crypto';

const DELIVERY_TABLE = 'feishu_group_relay_target_deliveries';
export const CHECKED_WEBHOOK_TYPES = new Set(['text']);

function textContent(msgType, content) {
	return msgType === 'text' ? String(content?.text ?? '') : '';
}

function fingerprint(text, keyword) {
	const withoutKeyword = String(text).split(String(keyword ?? '').trim()).join('');
	const sample = Array.from(withoutKeyword.replace(/\s/gu, '')).slice(0, 80).join('');
	return createHash('sha256').update(sample).digest('hex');
}

export function createDeliveryCheck({ ledger, bridgePost, targetRegistry, now = () => new Date() }) {
	const schema = [`CREATE TABLE IF NOT EXISTS ${DELIVERY_TABLE} (
		delivery_id bigserial PRIMARY KEY,
		source_message_id text NOT NULL,
		target_chat_id text NOT NULL,
		fingerprint text NOT NULL,
		sent_at timestamptz NOT NULL,
		target_message_id text,
		observed_at timestamptz,
		recalled_at timestamptz,
		recall_error text
	)`];

	async function recordWebhookDelivery({ messageId, targetChatId, msgType, content }) {
		if (!CHECKED_WEBHOOK_TYPES.has(msgType)) return;
		const target = await targetRegistry.get(targetChatId);
		await ledger.query(`INSERT INTO ${DELIVERY_TABLE} (source_message_id,target_chat_id,fingerprint,sent_at) VALUES($1,$2,$3,$4)`, [
			String(messageId), String(targetChatId), fingerprint(textContent(msgType, content), target?.keyword), now(),
		]);
	}

	async function observed(body) {
		if (String(body.msg_type ?? '').toLowerCase() !== 'text') return { status: 'ok', matched: false };
		const messageId = String(body.message_id);
		const replay = await ledger.query(`SELECT delivery_id FROM ${DELIVERY_TABLE} WHERE target_message_id=$1 LIMIT 1`, [messageId]);
		if (replay.rows.length) return { status: 'ok', matched: false };
		const target = (await targetRegistry.list()).find((item) => String(item.websocket_chat_id ?? '') === String(body.chat_id));
		if (!target) return { status: 'ok', matched: false };
		const digest = fingerprint(body.text, target.keyword);
		const observedAt = now();
		const candidates = await ledger.query(`SELECT delivery_id FROM ${DELIVERY_TABLE}
			WHERE target_chat_id=$1 AND target_message_id IS NULL AND sent_at >= $2 AND sent_at <= $3 AND fingerprint=$4
			ORDER BY sent_at`, [target.chat_id, new Date(observedAt.getTime() - 120_000), observedAt, digest]);
		if (candidates.rows.length !== 1) return { status: 'ok', matched: false };
		await ledger.query(`UPDATE ${DELIVERY_TABLE} SET target_message_id=$2,observed_at=$3 WHERE delivery_id=$1 AND target_message_id IS NULL`, [
			candidates.rows[0].delivery_id, messageId, observedAt,
		]);
		return { status: 'ok', matched: true };
	}

	async function recallDeliveries(sourceMessageId) {
		const result = await ledger.query(`SELECT delivery_id,target_chat_id,target_message_id FROM ${DELIVERY_TABLE}
			WHERE source_message_id=$1 AND target_message_id IS NOT NULL AND recalled_at IS NULL AND recall_error IS NULL
			ORDER BY delivery_id`, [String(sourceMessageId)]);
		for (const row of result.rows) {
			const target = await targetRegistry.get(row.target_chat_id);
			if (!target?.owned || !/^\d+$/.test(String(target.websocket_chat_id ?? ''))) continue;
			try {
				await bridgePost('/messages/recall-in-group', { chat_id: String(target.websocket_chat_id), message_id: row.target_message_id }, 30_000);
				await ledger.query(`UPDATE ${DELIVERY_TABLE} SET recalled_at=$2 WHERE delivery_id=$1`, [row.delivery_id, now()]);
			} catch (error) {
				await ledger.query(`UPDATE ${DELIVERY_TABLE} SET recall_error=$2 WHERE delivery_id=$1`, [row.delivery_id, error instanceof Error ? error.message : String(error)]);
			}
		}
	}

	async function sendObservedList() {
		try {
			const chats = (await targetRegistry.list())
				.filter((target) => /^\d+$/.test(String(target.websocket_chat_id ?? '')) && target.bot_id)
				.map((target) => ({ chat_id: String(target.websocket_chat_id), bot_ids: [String(target.bot_id)] }));
			await bridgePost('/targets/observe', { chats }, 30_000);
		} catch (error) {
			console.error(`目标群观察名单下发失败：${error instanceof Error ? error.message : String(error)}`);
		}
	}

	let started = false;
	let stopCurrent = null;
	async function start() {
		if (started) return stopCurrent ?? (() => {});
		started = true;
		const unsubscribe = targetRegistry.onChange(sendObservedList) ?? (() => {});
		await sendObservedList();
		const timer = setInterval(sendObservedList, 60_000);
		timer.unref?.();
		const stop = () => {
			if (!started) return;
			started = false;
			stopCurrent = null;
			clearInterval(timer);
			unsubscribe();
		};
		stopCurrent = stop;
		return stop;
	}

	async function deliveries(chatId) {
		const [counts, recent] = await Promise.all([
			ledger.query(`SELECT
				count(*) FILTER (WHERE target_message_id IS NOT NULL)::int AS matched,
				count(*) FILTER (WHERE target_message_id IS NULL)::int AS unmatched,
				count(*) FILTER (WHERE recalled_at IS NOT NULL)::int AS recalled,
				count(*) FILTER (WHERE recall_error IS NOT NULL)::int AS recall_failed
				FROM ${DELIVERY_TABLE} WHERE target_chat_id=$1 AND sent_at >= $2`, [String(chatId), new Date(now().getTime() - 24 * 60 * 60_000)]),
			ledger.query(`SELECT delivery_id,source_message_id,target_chat_id,fingerprint,sent_at,target_message_id,observed_at,recalled_at,recall_error
				FROM ${DELIVERY_TABLE} WHERE target_chat_id=$1 ORDER BY sent_at DESC LIMIT 50`, [String(chatId)]),
		]);
		return { counts: counts.rows[0], deliveries: recent.rows };
	}

	const routes = [
		{ method: 'POST', path: '/internal/larkagentx/target-observed', internal: true, handle: ({ body }) => observed(body) },
		{ method: 'GET', path: '/api/group-relay/target-groups/:chatId/deliveries', handle: ({ params }) => deliveries(params.chatId) },
	];

	return { schema, routes, start, recordWebhookDelivery, recallDeliveries };
}
