import * as Lark from '@larksuiteoapi/node-sdk';
import { createHash, randomUUID } from 'node:crypto';
import { readFileSync, mkdirSync, createWriteStream, readdirSync, statSync, existsSync } from 'node:fs';
import { open, readFile, unlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { createServer } from 'node:http';
import { Readable } from 'node:stream';
import { crossSiteWriteRejection } from './cross-site-guard.mjs';
import { createLedger } from './ledger.mjs';
import { releaseMetadata } from './release-metadata.mjs';
import { endWritable, writeChunk } from './stream-write.mjs';
import { singleFlight } from './single-flight.mjs';
import { createGroupSync, describeGroups, historyProgress } from './group-sync.mjs';
import { createGroupRelay, decryptLarkAgentXImage } from './group-relay.mjs';
import { bundleBaseName, bundleEntries, bundleRange, cardNeedsImageLookup, entryImageCount, historyFallback, historyRow, historySourceNote, mergeHistoryRows, resolveRouteChatIds, savedContentSummary, unnamedSendersByChat, writeHistoryBundle } from './history-bundle.mjs';
import { createWeChatGroupRelay } from './wechat-group-relay.mjs';
import { createSummaryListener } from './summary-listener.mjs';
import { createFeishuUserOauth } from './feishu-user-oauth.mjs';
import { createFeishuWorkbench } from './feishu-workbench.mjs';
import { createBaiduPanStorage } from './baidu-pan-storage.mjs';
import { createBaiduPanMarketArchive } from './baidu-pan-market-archive.mjs';
import { isSystemRelayPlaceholder } from './message-filter.mjs';
import { blockedMessageReason } from './content-filter.mjs';
import { extractImportContent, isValidDateTime } from './message-time.mjs';
import { hasImportableTaggedPayload } from './summary-ingestion-filter.mjs';
import { isOperatorPausedIngestion } from './ingestion-health.mjs';
import { shouldSkipMessageForward } from './message-idempotency.mjs';
import { shouldRedownloadRetryMedia } from './retry-media.mjs';
import { parsePaperIngestIds } from './paper-ingest-command.mjs';
import { cardPayload, renderLedgerTranscript } from './card-content.mjs';
import { parsePaperFeedback } from './paper-feedback-command.mjs';
import { parseXhsCommand } from './xhs-command.mjs';
import { matchResearchRoute } from './research-routes.mjs';
import { splitUtf8Text } from './media-chunks.mjs';
import { createRouteBinder, isSummaryChat, resolveLarkAgentXSource, hasLarkAgentXCardPayload, isDirectLarkAgentXRelayType, larkAgentXMessageType, normalizeLarkAgentXMessage, normalizeLarkAgentXRelayMessage, normalizeLarkAgentXSummaryMessage, normalizeLarkAgentXUnsupportedMessage, larkAgentXCardIsDegraded } from './larkagentx-ingress.mjs';
import { readLarkAgentXBackfill } from './larkagentx-backfill.mjs';
import { parseRelayMap, webhookConfigStatus } from './webhook-config.mjs';
import { createSourceTags } from './source-tags.mjs';
import { createWebhookTargets, WEBHOOK_ONLY_TARGET_ID } from './webhook-targets.mjs';
import { resolveFrontendAssetPath } from './frontend-assets.mjs';
import { postWebhookWithRetry, retryDelivery, safeDeliveryError } from './paper-delivery-retry.mjs';
import Busboy from 'busboy';

const required = ['FEISHU_APP_ID', 'FEISHU_APP_SECRET', 'N8N_TEXT_WEBHOOK_URL', 'N8N_MEDIA_PART_WEBHOOK_URL', 'N8N_MEDIA_FINALIZE_WEBHOOK_URL'];
for (const name of required) {
	if (!process.env[name]) throw new Error(`${name} must be configured`);
}

const { FEISHU_APP_ID: appId, FEISHU_APP_SECRET: appSecret } = process.env;
const textWebhookUrl = process.env.N8N_TEXT_WEBHOOK_URL;
const mediaWebhookUrl = process.env.N8N_MEDIA_WEBHOOK_URL;
const mediaPartWebhookUrl = process.env.N8N_MEDIA_PART_WEBHOOK_URL;
const mediaFinalizeWebhookUrl = process.env.N8N_MEDIA_FINALIZE_WEBHOOK_URL;
const mediaStateWebhookUrl = process.env.N8N_MEDIA_STATE_WEBHOOK_URL;
const quantServiceUrl = String(process.env.QUANT_SERVICE_URL ?? '').replace(/\/$/, '');
const quantWriteApiKey = String(process.env.QUANT_WRITE_API_KEY ?? '');
const quantAlertWebhookToken = String(process.env.QUANT_ALERT_WEBHOOK_TOKEN ?? '');
const feishuAlertReceiveId = String(process.env.FEISHU_ALERT_RECEIVE_ID ?? '').trim();
// Paper-KB has a separate outbound contract.  Falling back to the quant-alert
// route would silently post a reading report into the market-alert group.
const paperKbAlertWebhookToken = String(process.env.PAPER_KB_ALERT_WEBHOOK_TOKEN || quantAlertWebhookToken).trim();
const paperKbAlertReceiveId = String(process.env.PAPER_KB_FEISHU_CHAT_ID ?? '').trim();
// paper-kb: `收录 <arXiv ids>` in the reading group triggers ingestion through n8n.
const paperIngestWebhook = String(process.env.PAPER_KB_INGEST_WEBHOOK ?? '').trim();
const paperIngestChatId = String(process.env.PAPER_KB_FEISHU_CHAT_ID ?? '').trim();
const paperSearchWebhook = String(process.env.PAPER_KB_SEARCH_WEBHOOK ?? '').trim();
const paperFeedbackWebhook = String(process.env.PAPER_KB_FEEDBACK_WEBHOOK ?? '').trim();
const xhsCommandWebhook = String(process.env.XHS_COMMAND_WEBHOOK ?? 'http://127.0.0.1:5678/webhook/xhs-command').trim();
const xhsCommandChatId = String(process.env.XHS_COMMAND_CHAT_IDS ?? process.env.XHS_COMMAND_CHAT_ID ?? '').trim();
const larkAgentXIngressToken = String(process.env.LARKX_BRIDGE_TOKEN ?? '').trim();
const larkAgentXResourceUrl = String(process.env.LARKX_BRIDGE_RESOURCE_URL ?? '').trim();
const larkAgentXGroupRelayEnabled = String(process.env.LARKX_GROUP_RELAY_ENABLED ?? 'false').toLowerCase() === 'true';
const larkAgentXOfficialFallbackEnabled = String(process.env.LARKX_OFFICIAL_FALLBACK_ENABLED ?? 'false').toLowerCase() === 'true';
const larkAgentXCardBackfillEnabled = String(process.env.LARKX_CARD_BACKFILL_ENABLED ?? 'false').toLowerCase() === 'true';
const larkAgentXCardBackfillTags = new Set(String(process.env.LARKX_CARD_BACKFILL_TAGS ?? '').split(',').map((value) => value.trim().toLowerCase()).filter(Boolean));
const larkAgentXDynamicRouteDiscoveryEnabled = String(process.env.LARKX_DYNAMIC_ROUTE_DISCOVERY ?? 'false').toLowerCase() === 'true';
const larkAgentXGroupRelayRoutes = new Map(
	String(process.env.LARKX_GROUP_RELAY_ROUTES ?? '')
		.split(';')
		.map((pair) => pair.trim())
		.filter(Boolean)
		.map((pair) => {
			const separator = pair.indexOf('=');
			return separator > 0 ? [pair.slice(0, separator).trim(), pair.slice(separator + 1).trim()] : ['', ''];
		})
		.filter(([sourceKey, chatId]) => sourceKey && chatId),
);
const larkAgentXDynamicGroupRelayRoutes = new Map();
// Chats whose LARKX_GROUP_RELAY_ROUTES entry a verified bridge binding has
// contradicted; the learned route replaces it until the env is corrected.
const larkAgentXStaleStaticChats = new Set();
function larkAgentXSourceKeyForChat(chatId) {
	if (larkAgentXStaleStaticChats.has(chatId)) return larkAgentXDynamicGroupRelayRoutes.get(chatId);
	return larkAgentXGroupRelayRoutes.get(chatId) ?? larkAgentXDynamicGroupRelayRoutes.get(chatId);
}
const feishuAlertReceiveIdType = String(process.env.FEISHU_ALERT_RECEIVE_ID_TYPE ?? 'chat_id').trim();
const supportedAlertReceiveIdTypes = new Set(['chat_id', 'open_id', 'user_id', 'union_id']);
if (!supportedAlertReceiveIdTypes.has(feishuAlertReceiveIdType)) {
	throw new Error('FEISHU_ALERT_RECEIVE_ID_TYPE must be chat_id, open_id, user_id, or union_id');
}
// Unified ops notifier: one debounced channel for "the relay needs a human"
// signals (session needs re-scan, OAuth lane about to lapse, decode errors
// spiking). Routes through the official alert bot (FEISHU_ALERT_RECEIVE_ID);
// a dedicated LARKX_OPS_ALERT_RECEIVE_ID overrides the target. When neither is
// configured it logs only. Per-key debounce avoids repeat spam.
const opsAlertReceiveId = String(process.env.LARKX_OPS_ALERT_RECEIVE_ID ?? feishuAlertReceiveId ?? '').trim();
const opsAlertDebounceMs = Math.max(60_000, Number(process.env.LARKX_OPS_ALERT_DEBOUNCE_SECONDS ?? 1800) * 1000);
const opsAlertLastSent = new Map();
async function notifyOps(key, text) {
	const now = Date.now();
	const last = opsAlertLastSent.get(key) ?? 0;
	if (now - last < opsAlertDebounceMs) return false;
	opsAlertLastSent.set(key, now);
	console.warn(`[ops-alert ${key}] ${text}`);
	if (!opsAlertReceiveId) return false;
	try {
		await larkClient.im.v1.message.create({
			params: { receive_id_type: feishuAlertReceiveIdType },
			data: { receive_id: opsAlertReceiveId, msg_type: 'text', content: JSON.stringify({ text: `[relay 运维] ${text}` }) },
		});
		return true;
	} catch (error) {
		console.error(`ops 告警投递失败 key=${key}：${error instanceof Error ? error.message : String(error)}`);
		return false;
	}
}

const dashboardPort = Number(process.env.DASHBOARD_PORT ?? 3000);
const dashboardHost = String(process.env.DASHBOARD_HOST ?? '0.0.0.0').trim() || '0.0.0.0';
const larkAgentXHealthUrl = String(process.env.LARKX_BRIDGE_HEALTH_URL ?? 'http://127.0.0.1:8090/health').trim();
const routeBinder = createRouteBinder({ baseUrl: larkAgentXHealthUrl, token: larkAgentXIngressToken });
const longConnectionEnabled = String(process.env.FEISHU_LONG_CONNECTION_ENABLED ?? 'true').toLowerCase() !== 'false';
const frontendDist = process.env.FRONTEND_DIST ?? '/app/frontend-dist';
const quantFrontendDistConfigured = process.env.QUANT_FRONTEND_DIST ?? '/app/quant-frontend-dist';
const quantFrontendDist = existsSync(quantFrontendDistConfigured) ? quantFrontendDistConfigured : '/app/quant-frontend-dist';
const frontendMode = process.env.FRONTEND_MODE ?? (existsSync(frontendDist) ? 'spa' : 'legacy');
const itouguStatusFile = String(process.env.ITOUGU_STATUS_FILE ?? '/var/lib/itougu-neican/status.json').trim();
const itouguControlUrl = String(process.env.ITOUGU_CONTROL_URL ?? 'http://127.0.0.1:18084').trim().replace(/\/$/, '');
const importTimeZone = process.env.IMPORT_TIME_ZONE ?? 'Asia/Shanghai';
const remoteUploadPartBytes = 8 * 1024 * 1024;
const uploadPartBytes = Number(process.env.UPLOAD_PART_BYTES ?? remoteUploadPartBytes);
if (uploadPartBytes !== remoteUploadPartBytes) {
	throw new Error(`UPLOAD_PART_BYTES must match the remote import chunk limit: ${remoteUploadPartBytes}`);
}
// The SDK's default error logger includes an Axios request object. Suppress it
// so a failed resource download cannot place authorization headers in logs.
const larkLogger = {
	error: () => console.error('Feishu SDK request failed'),
	warn: () => {},
	info: () => {},
	debug: () => {},
	trace: () => {},
};
const larkClient = new Lark.Client({ appId, appSecret, domain: Lark.Domain.Feishu, logger: larkLogger });
const recentEvents = [];
const paperKbQueues = new Map();
const eventStreams = new Set();
const maxRecentEvents = 200;
const FEISHU_MESSAGE_UUID_MAX = 50;
function paperKbFeishuUuid(idempotencyKey) {
	const value = String(idempotencyKey ?? '').trim();
	if (!value) return undefined;
	if (value.length <= FEISHU_MESSAGE_UUID_MAX) return value;
	return `pk-${createHash('sha256').update(value, 'utf8').digest('hex').slice(0, FEISHU_MESSAGE_UUID_MAX - 3)}`;
}
const workbenchEventHandlers = new Map([
	['card.action.trigger', { label: '行动卡片回调', required_for: '可选行动卡片' }],
	['im.message.reaction.created_v1', { label: '表情协作回调', required_for: '可选行动卡片表情' }],
	['application.bot.menu_v6', { label: '机器人菜单回调', required_for: 'H5 工作台菜单' }],
]);
function noteWorkbenchEvent(eventType) {
	const current = workbenchEventHandlers.get(eventType);
	if (!current) return;
	workbenchEventHandlers.set(eventType, { ...current, received_count: Number(current.received_count ?? 0) + 1, last_received_at: new Date().toISOString() });
}
function workbenchEventStatus() {
	return [...workbenchEventHandlers.entries()].map(([event_type, value]) => ({
		event_type, ...value, handler_registered: true,
		state: value.last_received_at ? 'received' : 'awaiting_callback',
	}));
}

function annotateWorkbenchCapabilities(capabilities, oauth, applicationInspection) {
	const granted = new Set(oauth?.scope_audit?.granted_scopes ?? []);
	return (capabilities ?? []).map((capability) => {
		if (capability.key === 'application_inspection') {
			return {
				...capability,
				authorization_status: applicationInspection?.status === 'verified' ? 'verified' : applicationInspection?.status === 'missing_inspection_scope' ? 'missing' : 'awaiting_verification',
				missing_tenant_scopes: applicationInspection?.status === 'missing_inspection_scope' ? ['application:application:self_manage'] : [],
			};
		}
		if (capability.authorization_subject !== 'user') return capability;
		const missing = (capability.requires ?? []).filter((scope) => !granted.has(scope));
		return {
			...capability,
			authorization_status: granted.size ? (missing.length ? 'missing' : 'verified') : 'unknown',
			missing_user_scopes: missing,
		};
	});
}
const relayDrafts = new Map();
const feishuDedupeTtlMs = Number(process.env.FEISHU_DEDUPE_TTL_MS ?? 10 * 60 * 1000);
if (!Number.isFinite(feishuDedupeTtlMs) || feishuDedupeTtlMs < 0) {
	throw new Error('FEISHU_DEDUPE_TTL_MS must be a non-negative number');
}
const feishuEventPromises = new Map();
const sourceRegistryFile = process.env.SOURCE_REGISTRY_FILE ?? '/app/source-registry.json';
const sourceRegistry = JSON.parse(readFileSync(sourceRegistryFile, 'utf8'));
const ingestionStorageDir = process.env.INGESTION_STORAGE_DIR ?? '/var/lib/adapter-ingestion';
mkdirSync(ingestionStorageDir, { recursive: true });
const ledger = createLedger(process.env.INGESTION_DATABASE_URL || undefined);
await ledger.init(sourceRegistry);
// Registry tags plus the ones registered from the dashboard; `sourceRoutes` below is this map.
const sourceTags = createSourceTags({ registry: sourceRegistry, ledger });
await sourceTags.load();
const baiduPanEnabled = String(process.env.BAIDU_PAN_ENABLED ?? 'false').toLowerCase() === 'true';
const baiduPan = createBaiduPanStorage({
	appKey: process.env.BAIDU_PAN_APP_KEY,
	secretKey: process.env.BAIDU_PAN_SECRET_KEY,
	redirectUri: String(process.env.BAIDU_PAN_REDIRECT_URI ?? 'oob').trim() || 'oob',
	ledger,
	rootPath: String(process.env.BAIDU_PAN_ROOT_PATH ?? '/apps/股票paper存储/feishu-relay').trim(),
	spoolDir: String(process.env.BAIDU_PAN_SPOOL_DIR ?? '').trim(),
	maxUploadBytes: Number(process.env.BAIDU_PAN_MAX_UPLOAD_BYTES ?? 500 * 1024 * 1024),
	sliceBytes: Number(process.env.BAIDU_PAN_SLICE_BYTES ?? 4 * 1024 * 1024),
});
const baiduPanMarketArchive = createBaiduPanMarketArchive({
	baiduPan, ledger, quantServiceUrl, quantWriteApiKey,
	enabled: String(process.env.BAIDU_PAN_MARKET_ARCHIVE_ENABLED ?? 'false').toLowerCase() === 'true',
	rawOverflowEnabled: String(process.env.BAIDU_PAN_RAW_OVERFLOW_ENABLED ?? 'false').toLowerCase() === 'true',
	rawCapabilities: String(process.env.BAIDU_PAN_RAW_OVERFLOW_CAPABILITIES ?? 'a_share_prices_snapshot,realtime_quote,order_book_quote,rt_k,rt_min,rt_min_daily').split(','),
	rawBatchRows: Number(process.env.BAIDU_PAN_RAW_OVERFLOW_BATCH_ROWS ?? 100),
	rawBatchesPerDrain: Number(process.env.BAIDU_PAN_RAW_OVERFLOW_BATCHES_PER_DRAIN ?? 4),
	intervalSeconds: Number(process.env.BAIDU_PAN_MARKET_ARCHIVE_INTERVAL_SECONDS ?? 30),
	rootPath: String(process.env.BAIDU_PAN_MARKET_ARCHIVE_ROOT_PATH ?? '/apps/股票paper存储/market-realtime').trim(),
	rawRootPath: String(process.env.BAIDU_PAN_RAW_OVERFLOW_ROOT_PATH ?? '/apps/股票paper存储/quant-lake/v1/raw-overflow').trim(),
});
const feishuUserOauth = createFeishuUserOauth({
	appId, appSecret, ledger, redirectUri: String(process.env.FEISHU_USER_OAUTH_REDIRECT_URI ?? 'http://localhost:8080/callback').trim(),
});
const groupRelayIntervalSeconds = Number(process.env.FEISHU_GROUP_RELAY_INTERVAL_SECONDS ?? 10);
if (!Number.isFinite(groupRelayIntervalSeconds) || groupRelayIntervalSeconds < 10 || groupRelayIntervalSeconds > 30) {
	throw new Error('FEISHU_GROUP_RELAY_INTERVAL_SECONDS must be between 10 and 30');
}
const groupRelaySourceConcurrency = Number(process.env.FEISHU_GROUP_RELAY_SOURCE_CONCURRENCY ?? 3);
if (!Number.isFinite(groupRelaySourceConcurrency) || groupRelaySourceConcurrency < 1 || groupRelaySourceConcurrency > 8) {
	throw new Error('FEISHU_GROUP_RELAY_SOURCE_CONCURRENCY must be between 1 and 8');
}
const groupRelayHistoryLookbackSeconds = Number(process.env.FEISHU_GROUP_RELAY_HISTORY_LOOKBACK_SECONDS ?? 300);
if (!Number.isFinite(groupRelayHistoryLookbackSeconds) || groupRelayHistoryLookbackSeconds < 60 || groupRelayHistoryLookbackSeconds > 3600) {
	throw new Error('FEISHU_GROUP_RELAY_HISTORY_LOOKBACK_SECONDS must be between 60 and 3600');
}
const groupRelayBootstrapMode = String(process.env.FEISHU_GROUP_RELAY_BOOTSTRAP_MODE ?? 'skip_existing').trim();
if (!['skip_existing', 'forward_existing'].includes(groupRelayBootstrapMode)) {
	throw new Error('FEISHU_GROUP_RELAY_BOOTSTRAP_MODE must be skip_existing or forward_existing');
}
// "chat_id=url;chat_id2=url2" — a target configured here is delivered
// through a custom-bot webhook (no monthly API quota cost) instead of
// im/v1/messages, at the cost of edit/recall sync for that one target
// (see group-relay.mjs's WEBHOOK_SENT_PREFIX). Same format as the Python
// relays' ITOUGU_FEISHU_WEBHOOKS / WECHAT_BIZ_FEISHU_WEBHOOKS.
const groupRelayWebhooksByChatId = parseRelayMap(process.env.FEISHU_GROUP_RELAY_WEBHOOKS);
const groupRelayWebhookKeywordsByChatId = parseRelayMap(process.env.FEISHU_GROUP_RELAY_WEBHOOK_KEYWORDS);
// Targets entered in the dashboard are added to the same two maps, so the relay needs no restart for them.
// `bots` is how a target gets its bot without anyone making one by hand: the
// bridge has the account's own session create the group's relay bot.
const webhookTargets = createWebhookTargets({
	ledger, secret: appSecret, webhooks: groupRelayWebhooksByChatId, keywords: groupRelayWebhookKeywordsByChatId,
	bots: {
		ensure: ({ chatId, keyword }) => bridgePostJson('/chat-bots/ensure', { chat_id: chatId, keyword }, 60_000),
		remove: ({ chatId, botId }) => bridgePostJson('/chat-bots/remove', { chat_id: chatId, bot_id: botId }, 30_000),
	},
});
const webhookTargetsLoaded = await webhookTargets.load();
if (webhookTargetsLoaded.unreadable) console.error(`有 ${webhookTargetsLoaded.unreadable} 个看板配置的目标群 webhook 无法解密，需要在看板里重新填写`);
const groupRelayConfig = {
	enabled: String(process.env.FEISHU_GROUP_RELAY_ENABLED ?? 'true').toLowerCase() !== 'false',
	webhooksByChatId: groupRelayWebhooksByChatId,
	webhookKeywordsByChatId: groupRelayWebhookKeywordsByChatId,
	larkAgentXResourceUrl,
	larkAgentXToken: larkAgentXIngressToken,
	targetChatId: String(process.env.FEISHU_GROUP_RELAY_TARGET_CHAT_ID ?? '').trim(),
	intervalSeconds: groupRelayIntervalSeconds,
	sourceConcurrency: Math.floor(groupRelaySourceConcurrency),
	actionCardsEnabled: String(process.env.FEISHU_GROUP_RELAY_ACTION_CARDS_ENABLED ?? 'false').toLowerCase() === 'true',
	// "#tag <Beijing forwarding time>" header on every relayed message.
	relayTimestampEnabled: String(process.env.FEISHU_GROUP_RELAY_TIMESTAMP_ENABLED ?? 'true').toLowerCase() !== 'false',
	// Outbound messages are card JSON 2.0; set to false to fall back to the
	// text/post bubbles the relay sent before 2026-09-08.
	outboundCard: String(process.env.FEISHU_GROUP_RELAY_OUTBOUND_CARD ?? 'true').toLowerCase() !== 'false',
	historyLookbackSeconds: groupRelayHistoryLookbackSeconds,
	overlapSeconds: Math.min(120, Math.max(30, Math.floor(groupRelayIntervalSeconds * 3))),
	reconcileEverySeconds: Math.max(3600, Number(process.env.FEISHU_GROUP_RELAY_RECONCILE_SECONDS ?? 21_600)),
	reconcileLookbackSeconds: Math.max(300, Number(process.env.FEISHU_GROUP_RELAY_RECONCILE_LOOKBACK_SECONDS ?? 86_400)),
	bootstrapMode: groupRelayBootstrapMode,
	sources: [
		{ key: 'anqiang', tag: 'anqiang', chatId: String(process.env.FEISHU_GROUP_RELAY_ANQIANG_CHAT_ID ?? 'oc_1de6464db12c43bf60985f7131e6334a').trim(), chatName: String(process.env.FEISHU_GROUP_RELAY_ANQIANG_CHAT_NAME ?? '马安强 (1)').trim() },
		{ key: 'liwei', tag: 'liwei', chatId: String(process.env.FEISHU_GROUP_RELAY_LIWEI_CHAT_ID ?? 'oc_4ed9fdb72a152bc921b2d34bd4a1df14').trim(), chatName: String(process.env.FEISHU_GROUP_RELAY_LIWEI_CHAT_NAME ?? '消息更新群').trim(), targetChatIds: String(process.env.FEISHU_GROUP_RELAY_LIWEI_TARGET_CHAT_IDS ?? '').split(',').map((value) => value.trim()).filter(Boolean) },
		{ key: 'quanneng', tag: 'quanneng', chatId: String(process.env.FEISHU_GROUP_RELAY_QUANNENG_CHAT_ID ?? 'oc_d6a89890c3a62517116a2c63f015e0a0').trim(), chatName: String(process.env.FEISHU_GROUP_RELAY_QUANNENG_CHAT_NAME ?? '新野人哥会员群【禁言】').trim() },
		// 小杰夜报～ 由用户 OAuth 按群名解析；拿到稳定 chat_id 后可通过
		// FEISHU_GROUP_RELAY_XIAOJIE_CHAT_ID 固定，避免同名群误匹配。
		{ key: 'xiaojie', tag: 'xiaojie', chatId: String(process.env.FEISHU_GROUP_RELAY_XIAOJIE_CHAT_ID ?? '').trim(), chatName: String(process.env.FEISHU_GROUP_RELAY_XIAOJIE_CHAT_NAME ?? '小杰夜报～').trim() },
	],
};
const wechatGroupRelayConfig = {
	enabled: String(process.env.WECHAT_GROUP_RELAY_ENABLED ?? 'false').toLowerCase() === 'true',
	outboundCard: String(process.env.FEISHU_GROUP_RELAY_OUTBOUND_CARD ?? 'true').toLowerCase() !== 'false',
	sourceKey: String(process.env.WECHAT_GROUP_RELAY_SOURCE_KEY ?? 'wechat_xiaolan').trim() || 'wechat_xiaolan',
	sourceChatId: String(process.env.WECHAT_GROUP_RELAY_SOURCE_CHAT_ID ?? '50136408612@chatroom').trim(),
	routeTag: String(process.env.WECHAT_GROUP_RELAY_ROUTE_TAG ?? 'xiaolan').trim() || 'xiaolan',
	targetChatId: String(process.env.WECHAT_GROUP_RELAY_TARGET_CHAT_ID ?? '').trim() || groupRelayConfig.targetChatId,
	endpointToken: String(process.env.WECHAT_GROUP_RELAY_ENDPOINT_TOKEN ?? '').trim(),
	maxTextLength: Math.max(100, Math.min(10_000, Number(process.env.WECHAT_GROUP_RELAY_MAX_TEXT_LENGTH ?? 3500))),
};
// This guards a copied relay ledger during a deliberate failover/failback.
// It is not presented as a cross-host distributed lock: the runbooks still
// fence the old machine before promoting the new writer generation.
const relayWriterId = String(process.env.FEISHU_RELAY_WRITER_ID ?? '').trim();
const relayWriterFence = relayWriterId ? () => ledger.relayWriterFence(relayWriterId) : null;
const summaryListenerIntervalSeconds = Number(process.env.FEISHU_SUMMARY_LISTENER_INTERVAL_SECONDS ?? groupRelayIntervalSeconds);
if (!Number.isFinite(summaryListenerIntervalSeconds) || summaryListenerIntervalSeconds < 10 || summaryListenerIntervalSeconds > 30) {
	throw new Error('FEISHU_SUMMARY_LISTENER_INTERVAL_SECONDS must be between 10 and 30');
}
const summaryListenerHistoryLookbackSeconds = Number(process.env.FEISHU_SUMMARY_LISTENER_HISTORY_LOOKBACK_SECONDS ?? 3600);
if (!Number.isFinite(summaryListenerHistoryLookbackSeconds) || summaryListenerHistoryLookbackSeconds < 60 || summaryListenerHistoryLookbackSeconds > 3600) {
	throw new Error('FEISHU_SUMMARY_LISTENER_HISTORY_LOOKBACK_SECONDS must be between 60 and 3600');
}
const summaryListenerBootstrapMode = String(process.env.FEISHU_SUMMARY_LISTENER_BOOTSTRAP_MODE ?? 'forward_existing').trim();
if (!['skip_existing', 'forward_existing'].includes(summaryListenerBootstrapMode)) {
	throw new Error('FEISHU_SUMMARY_LISTENER_BOOTSTRAP_MODE must be skip_existing or forward_existing');
}
const summaryListenerConfig = {
	enabled: String(process.env.FEISHU_SUMMARY_LISTENER_ENABLED ?? 'true').toLowerCase() !== 'false',
	key: 'summary-group', chatId: groupRelayConfig.targetChatId,
	intervalSeconds: summaryListenerIntervalSeconds, historyLookbackSeconds: summaryListenerHistoryLookbackSeconds,
	overlapSeconds: Math.min(120, Math.max(30, Math.floor(summaryListenerIntervalSeconds * 3))),
	bootstrapMode: summaryListenerBootstrapMode, sourceLabel: '分析师发送汇总群',
};
await ledger.initializeRelayRoutes(groupRelayConfig.sources);
const feishuWorkbench = createFeishuWorkbench({
	appId, appSecret, larkClient, ledger, userRequest: feishuUserOauth.userRequest, sourceApi: feishuUserOauth.sourceApi,
	config: {
		targetChatId: groupRelayConfig.targetChatId,
		publicBaseUrl: String(process.env.FEISHU_WORKBENCH_PUBLIC_BASE_URL ?? '').trim(),
		driveFolderToken: String(process.env.FEISHU_WORKBENCH_DRIVE_FOLDER_TOKEN ?? '').trim(),
		driveMaxFileBytes: Math.max(30 * 1024 * 1024, Number(process.env.FEISHU_WORKBENCH_DRIVE_MAX_FILE_BYTES ?? 524_288_000)),
		archiveProvider: String(process.env.BAIDU_PAN_ARCHIVE_PROVIDER ?? 'auto').trim().toLowerCase(),
		baiduPanEnabled,
		wikiSpaceId: String(process.env.FEISHU_WORKBENCH_WIKI_SPACE_ID ?? '').trim(),
		wikiParentNodeToken: String(process.env.FEISHU_WORKBENCH_WIKI_PARENT_NODE_TOKEN ?? '').trim(),
		tasklistGuid: String(process.env.FEISHU_WORKBENCH_TASKLIST_GUID ?? '').trim(),
		baseAppToken: String(process.env.FEISHU_WORKBENCH_BASE_APP_TOKEN ?? '').trim(),
		baseTableId: String(process.env.FEISHU_WORKBENCH_BASE_TABLE_ID ?? '').trim(),
		calendarId: String(process.env.FEISHU_WORKBENCH_CALENDAR_ID ?? '').trim(),
		approvalCode: String(process.env.FEISHU_WORKBENCH_APPROVAL_CODE ?? '').trim(),
		ailyAppId: String(process.env.FEISHU_WORKBENCH_AILY_APP_ID ?? '').trim(),
		asrEngineType: String(process.env.FEISHU_WORKBENCH_ASR_ENGINE_TYPE ?? '16k_auto').trim(),
		actionCardsEnabled: groupRelayConfig.actionCardsEnabled,
	},
	baiduPan,
});
const groupRelay = createGroupRelay({
	larkClient, sourceApi: feishuUserOauth.sourceApi, ledger, workbench: feishuWorkbench,
	config: { ...groupRelayConfig, sourcesProvider: () => ledger.relayRoutes() }, canWrite: relayWriterFence,
});
const wechatGroupRelay = wechatGroupRelayConfig.enabled && wechatGroupRelayConfig.targetChatId
	? createWeChatGroupRelay({ larkClient, ledger, config: wechatGroupRelayConfig })
	: null;
const summaryListener = createSummaryListener({
	sourceApi: feishuUserOauth.sourceApi, ledger, processMessage: processSummaryGroupMessage,
	config: summaryListenerConfig, canWrite: relayWriterFence,
});
setInterval(() => { void groupRelay.tick(); }, groupRelayIntervalSeconds * 1000).unref();
void groupRelay.tick();
// Keeps the joined-group directory, each bound group's membership and a stored
// copy of its history current.  Read-only towards Feishu; a few gateway pages
// per pass, so a newly bound group is read in the background, not in a burst.
const historyRowFromBridge = (payload) => historyRow(payload, { normalize: normalizeLarkAgentXRelayMessage, describeUnsupported: normalizeLarkAgentXUnsupportedMessage, isDegradedCard: larkAgentXCardIsDegraded });
const groupSync = createGroupSync({
	ledger, toRow: historyRowFromBridge, log: (line) => console.log(line),
	// The account left the group: nothing can arrive on this route any more.
	// The route stops and its WebSocket binding goes, but it stays listed as
	// left, so what it relayed can still be exported.
	archiveRoute: async (sourceKey, { reason, chatIds }) => {
		const route = await ledger.archiveRelayRoute(sourceKey, { reason, chatIds });
		if (!route) return null;
		await routeBinder.unbind(sourceKey);
		return { key: route.key, tag: route.tag, chat_name: route.chatName };
	},
	// The account is back in the group: the route resumes on the same group.
	restoreRoute: async (sourceKey, chatId) => {
		const route = await ledger.restoreRelayRoute(sourceKey);
		if (!route) return null;
		const binding = await routeBinder.bind(route, { websocketChatId: chatId });
		console.log(`退群路由已恢复 source_key=${sourceKey} binding=${binding.status}${binding.message ? ` ${binding.message}` : ''}`);
		return { key: route.key };
	},
	bridge: {
		joined: () => joinedGroupsWithRoutes(true),
		membership: (chatIds) => bridgePostJson('/chats/membership', { chat_ids: chatIds }, 60_000),
		page: (chatId, start, end) => bridgePostJson('/history/page', { chat_id: chatId, start, end }, 60_000),
	},
});
// Remove a route and its WebSocket binding.  The relayed messages and the
// stored history of its group stay in the database.
async function removeRelayRoute(sourceKey) {
	const route = (await ledger.relayRoutes()).find((item) => item.key === sourceKey);
	if (!route || !(await ledger.deleteRelayRoute(sourceKey))) return null;
	await routeBinder.unbind(sourceKey);
	return route;
}

// The bridge knows the bindings it saved; the routes declared in this
// adapter's environment are known only here.  Both are bound groups, and only
// a group whose route still exists counts.
async function joinedGroupsWithRoutes(refresh) {
	const [joined, byKey, routes] = await Promise.all([bridgePostJson('/chats/joined', { refresh: Boolean(refresh) }, 60_000), numericChatIdsByRoute(), ledger.relayRoutes()]);
	// An archived route (its group was left) is not bound; its groups are
	// reported separately, so they are still checked and the route can resume.
	const routeByKey = new Map(routes.filter((route) => !route.archivedAt).map((route) => [route.key, route]));
	const bound = new Map();
	for (const item of joined?.bound ?? []) if (routeByKey.has(item.source_key)) bound.set(String(item.chat_id), item);
	for (const [sourceKey, chatIds] of byKey) {
		if (!routeByKey.has(sourceKey)) continue;
		for (const chatId of chatIds) if (!bound.has(chatId)) bound.set(chatId, { chat_id: chatId, source_key: sourceKey, chat_name: routeByKey.get(sourceKey).chatName ?? '' });
	}
	const archived = routes.filter((route) => route.archivedAt).flatMap((route) => route.archivedChatIds.filter((chatId) => /^\d+$/.test(chatId) && !bound.has(chatId))
		.map((chatId) => ({ chat_id: chatId, source_key: route.key, chat_name: route.chatName ?? '' })));
	return { groups: joined?.groups ?? [], bound: [...bound.values()], archived };
}

async function runGroupSync() {
	if (!larkAgentXIngressToken) return;
	// One writer keeps the stored state; a second adapter on the same database only reads it.
	if (relayWriterFence && !(await relayWriterFence().catch(() => ({ allowed: false }))).allowed) return;
	await groupSync.tick();
}
setInterval(() => { void runGroupSync(); }, 30_000).unref();
setTimeout(() => { void runGroupSync(); }, 15_000).unref();
if (baiduPanMarketArchive.enabled) {
	setInterval(() => { void baiduPanMarketArchive.poll(); }, baiduPanMarketArchive.intervalMs).unref();
	void baiduPanMarketArchive.poll();
}
setInterval(() => { void summaryListener.tick(); }, summaryListenerIntervalSeconds * 1000).unref();
void summaryListener.tick();
const reconcileSeconds = Math.max(30, Number(process.env.INGESTION_RECONCILE_SECONDS ?? 300));
const ledgerRetentionDays = Math.max(7, Number(process.env.INGESTION_LEDGER_RETENTION_DAYS ?? 90));
let lastLedgerPruneAt = 0;
async function runLocalAnalysisQueue() {
	try {
		for (const task of await ledger.pendingAnalysis()) {
			// The archive at 47 is the sole analyst-opinion source.  Local Feishu
			// media/text ingestion remains durable transport only: it must not run
			// a second OCR/ASR/text-opinion pipeline or create competing claims.
			await ledger.completeAnalysis(task.analysis_id, {
				kind: 'remote-archive-source', remote_batch_id: task.remote_batch_id,
				message: '等待远端市场复盘档案完成解析；量化观点仅由远端报告同步工作流写入。',
				generated_at: new Date().toISOString(), quant_service_configured: Boolean(quantServiceUrl),
			});
		}
	} catch (error) { console.error(`本地分析队列失败：${error instanceof Error ? error.message : String(error)}`); }
}
const deliveryWorkerId = `feishu-adapter-${process.pid}`;
const runDeliveryQueue = singleFlight(async () => {
	// The remote archive admits one active import registration per analyst. Keep
	// this edge worker serial so a replay cannot make its own idempotency state
	// conflict, while the durable outbox retains all later deliveries.
	for (const delivery of await ledger.claimDeliveries({ workerId: deliveryWorkerId, limit: 1 })) {
		const queued = await ledger.getJobDelivery(delivery.job_id);
		if (!queued) { await ledger.completeDelivery(delivery.delivery_id); continue; }
		try {
			const payload = queued.payload ?? {};
			const replayImportContent = payload.content_date
				? { content: payload.import_content ?? '', content_date: payload.content_date, content_time: payload.content_time }
				: extractImportContent(payload.message_text, { referenceTime: payload.receivedAt });
			const originalResources = Array.isArray(payload.resources) ? payload.resources : [];
				const replayResources = (queued.resources ?? []).map(({ asset, parts }) => ({
				asset_id: asset.asset_id, property: `replay_${asset.ordinal}`, filename: filenameForMediaType(asset.filename, asset.media_type), media_type: asset.media_type,
				declared_bytes: Number(asset.declared_bytes), content_sha256: asset.content_sha256,
				path: asset.storage_path, remote_upload_id: asset.remote_upload_id,
				last_modified: Number(originalResources[Number(asset.ordinal)]?.last_modified ?? Date.now()), part_size: uploadPartBytes,
					part_count: parts.length, parts: parts.map((part) => ({ part_index: Number(part.part_index), property: `replay_${asset.ordinal}_part_${part.part_index}`, bytes: Number(part.bytes), sha256: part.sha256, uploaded: Boolean(part.uploaded), remote_status: part.remote_status })),
				}));
				const localResourcesAvailable = originalResources.length > 0
					&& replayResources.length === originalResources.length
					&& replayResources.every((resource) => resource.path && existsSync(resource.path));
				const redownloadMedia = Number(delivery.attempt_count) > 1 && shouldRedownloadRetryMedia({
					expectedResourceCount: originalResources.length, event: payload.event, localResourcesAvailable,
				});
			const deliveryResources = redownloadMedia
				? await downloadMedia(payload.event, feishuUserOauth.sourceApi)
				: replayResources;
			if (redownloadMedia) console.info(`重试时已从飞书重新下载媒体：${queued.message_id}`);
			if (deliveryResources.some((resource) => resource.path && !existsSync(resource.path))) throw new Error('本地重试文件已被清理，且无法从飞书恢复上传');
			// Older versions admitted a tag-only summary bubble and let the n8n
			// parser fail it.  A retry must not keep turning that non-content into
			// a remote error: retain the ledger evidence, mark it filtered, and
			// complete its outbox item without creating a remote batch.
			if (!deliveryResources.length && !String(replayImportContent.content ?? '').trim()) {
				await ledger.updateJob(queued.job_id, { status: 'filtered', stage: 'empty_tagged_payload', error_class: null, error_message: null });
				await ledger.completeDelivery(delivery.delivery_id);
				continue;
			}
			await hydrateRemotePartState(deliveryResources);
			await dispatchToN8n(payload.event, {
				resources: deliveryResources, messageText: payload.message_text, sourceLabel: payload.source_label,
				replayJobId: queued.job_id, source: payload.source, remoteBatchId: queued.remote_batch_id,
				receivedAt: payload.receivedAt,
				importContent: replayImportContent,
			});
			await ledger.completeDelivery(delivery.delivery_id);
		} catch (error) {
				// A workflow can persist the remote status before its webhook returns 500.
				// Keep that diagnostic instead of obscuring it as a local retry failure.
				const current = await ledger.getJob(queued.job_id);
				const retryable = current?.status !== 'failed';
				if (!current || !['failed', 'retryable_failed'].includes(current.status)) {
					await ledger.updateJob(queued.job_id, { status: 'retryable_failed', stage: 'retry_failed', error_class: 'local_retry', error_message: error instanceof Error ? error.message : String(error) });
				}
				await ledger.failDelivery(delivery.delivery_id, { retryable, errorMessage: error instanceof Error ? error.message : String(error) });
		}
	}
});
async function cleanupUnreferencedMedia() {
	try {
		const referenced = await ledger.referencedStoragePaths();
		for (const name of readdirSync(ingestionStorageDir)) {
			const path = join(ingestionStorageDir, name);
			if (!referenced.has(path)) { try { const stat = statSync(path); if (Date.now() - stat.mtimeMs > reconcileSeconds * 1000) await unlink(path); } catch {} }
		}
	} catch (error) { console.error(`本地媒体对账失败：${error instanceof Error ? error.message : String(error)}`); }
}
async function reconcileNow() {
	await runDeliveryQueue();
	await runLocalAnalysisQueue();
	await cleanupUnreferencedMedia();
	if (Date.now() - lastLedgerPruneAt >= 60 * 60 * 1000) {
		await ledger.pruneHistory(ledgerRetentionDays);
		lastLedgerPruneAt = Date.now();
	}
	const [jobs, analysis] = await Promise.all([ledger.pendingJobs(), ledger.pendingAnalysis()]);
	return { pending_jobs: jobs.length, pending_analysis: analysis.length, reconciled_at: new Date().toISOString() };
}
setInterval(() => {
	void reconcileNow().catch((error) => console.error(`统一对账失败：${error instanceof Error ? error.message : String(error)}`));
}, reconcileSeconds * 1000).unref();
setInterval(() => { void cleanupUnreferencedMedia(); }, reconcileSeconds * 1000).unref();
setInterval(runLocalAnalysisQueue, Math.max(30, Number(process.env.ANALYSIS_POLL_SECONDS ?? 60) * 1000)).unref();
setInterval(() => { void runDeliveryQueue(); }, 10_000).unref();
// OAuth keepalive: Feishu now grants only a ~7-day refresh window and rotates
// it on use, so a quiet stretch with no backfill traffic can let it lapse.
// Daily, if the refresh token is within FEISHU_OAUTH_KEEPALIVE_DAYS of expiry,
// refresh it proactively (no-op when it is comfortably in the future or when
// keepalive is disabled). Never throws into the loop.
const oauthKeepaliveDays = Number(process.env.FEISHU_OAUTH_KEEPALIVE_DAYS ?? 3);
if (oauthKeepaliveDays > 0) {
	setInterval(() => {
		void (async () => {
			try {
				const oauth = await feishuUserOauth.status();
				if (!oauth.configured || !oauth.refresh_expires_at) return;
				const remainingMs = Date.parse(oauth.refresh_expires_at) - Date.now();
				if (Number.isFinite(remainingMs) && remainingMs < oauthKeepaliveDays * 24 * 3600 * 1000) {
					await feishuUserOauth.forceRefresh();
					console.log(`飞书用户 OAuth 保活：refresh token 临近过期，已主动续期`);
				}
			} catch (error) {
				console.error(`飞书用户 OAuth 保活失败：${error instanceof Error ? error.message : String(error)}`);
			}
		})();
	}, 24 * 3600 * 1000).unref();
}

// Health watch: turn the silent-failure signals into one debounced ops alert.
let needsLoginSince = 0;
setInterval(() => {
	void (async () => {
		try {
			const larkagentx = await larkAgentXDashboardStatus();
			const authState = larkagentx?.auth?.state;
			if (authState === 'needs_login') {
				if (!needsLoginSince) needsLoginSince = Date.now();
				if (Date.now() - needsLoginSince > 120_000) {
					await notifyOps('needs_login', '个人会话已失效，需要在控制台扫码重新登录（监听已暂停）。');
				}
			} else {
				needsLoginSince = 0;
				if (authState === 'degrading') {
					await notifyOps('auth_degrading', '个人会话票据探针失败，WS 仍在但即将失效，建议尽快重新扫码。');
				}
			}
			const oauth = await feishuUserOauth.status();
			if (oauth.configured && oauth.refresh_expires_at) {
				const remainingMs = Date.parse(oauth.refresh_expires_at) - Date.now();
				if (Number.isFinite(remainingMs) && remainingMs < 48 * 3600 * 1000) {
					await notifyOps('oauth_expiry', `飞书用户 OAuth 补读授权将在 ${Math.max(0, Math.floor(remainingMs / 3600000))} 小时后过期，请在控制台重新授权。`);
				}
			}
		} catch (error) {
			console.error(`relay 健康自检失败：${error instanceof Error ? error.message : String(error)}`);
		}
	})();
}, Math.max(60, Number(process.env.LARKX_HEALTH_WATCH_SECONDS ?? 60)) * 1000).unref();
void reconcileNow().catch((error) => console.error(`启动对账失败：${error instanceof Error ? error.message : String(error)}`));
const sourceRoutes = sourceTags.routes;
const supportedMediaTypes = new Set([
	'image/jpeg', 'image/png', 'image/webp', 'audio/mp4', 'audio/x-m4a',
	'audio/mpeg', 'audio/wav', 'audio/x-wav', 'video/mp4', 'video/quicktime',
]);

function parseContent(content) {
	try {
		return JSON.parse(content ?? '{}');
	} catch {
		return { raw: content };
	}
}

function resolveRoute(tag) {
	const normalized = String(tag ?? '').trim().toLowerCase();
	const route = sourceRoutes.get(normalized);
	if (!route || route.enabled === false) throw new Error(`不支持的来源标签：#${normalized || '未填写'}`);
	return route;
}

function routeFromMessageText(messageText) {
	const match = String(messageText ?? '').match(/^#([a-z0-9-]+)/i);
	return resolveRoute(match?.[1]);
}

// Rendered when the page is served: tags can be registered while running, and a
// label typed in the dashboard is text, not markup.
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const RELAY_ROUTE_OPTIONS_SLOT = '<!--relay-route-options-->';
const relayRouteOptions = () => [...sourceRoutes.values()]
	.filter((route) => route.enabled !== false)
	.map((route) => `<option value="${escapeHtml(route.tag)}">#${escapeHtml(route.tag)} · ${escapeHtml(route.label)}</option>`)
	.join('');

function extractPostPayload(content) {
	const blocks = Array.isArray(content?.content_v2) ? content.content_v2 : content?.content;
	if (!Array.isArray(blocks)) return { text: '', resources: [] };
	const text = [];
	const resources = [];
	for (const line of blocks) {
		const lineText = [];
		for (const element of Array.isArray(line) ? line : []) {
			if (element?.tag === 'text' && typeof element.text === 'string') lineText.push(element.text);
			if (element?.tag === 'img' && element.image_key) {
				resources.push({ key: element.image_key, resource_type: 'image', ...(element.larkagentx_resource ? { larkagentx_resource: element.larkagentx_resource } : {}) });
			}
			if ((element?.tag === 'media' || element?.tag === 'audio') && element.file_key) {
				resources.push({ key: element.file_key, resource_type: 'file' });
			}
		}
		if (lineText.length) text.push(lineText.join(''));
	}
	return { text: text.join('\n'), resources };
}

function extractMessagePayload(message) {
	const content = parseContent(message?.content);
	if (message?.message_type === 'post') return extractPostPayload(content);
	// A relayed card (and any card read with card_msg_content_type=
	// user_card_content) carries its route tag as the first line of its text.
	if (message?.message_type === 'interactive') {
		const card = cardPayload(content);
		// Through the WebSocket a card's pictures arrive as keys beside the card,
		// not as portable image keys inside it.
		const beside = (Array.isArray(message.larkagentx_images) ? message.larkagentx_images : [])
			.filter((resource) => resource?.image_id && !card.resources.some((existing) => existing.key === resource.image_id))
			.map((resource) => ({ key: resource.image_id, resource_type: 'image', larkagentx_resource: resource }));
		return beside.length ? { ...card, resources: [...card.resources, ...beside] } : card;
	}
	const resources = [];
	if (content.image_key) resources.push({ key: content.image_key, resource_type: 'image', ...(content.larkagentx_resource ? { larkagentx_resource: content.larkagentx_resource } : {}) });
	if (content.file_key) resources.push({ key: content.file_key, resource_type: 'file' });
	// Native file messages cannot carry a text paragraph. Group relay preserves
	// their source route in the filename (`#tag original-file`), so recover it
	// here before deciding whether the message is importable.
	const fileRoute = typeof content.file_name === 'string' ? content.file_name.match(/^(#[a-z0-9-]+)(?=\s|$)/i)?.[1] : null;
	return { text: content.text ?? fileRoute ?? content.raw ?? null, resources };
}

function summarizeEvent(data) {
	const message = data.message ?? {};
	const payload = extractMessagePayload(message);
	return {
		event_id: data.event_id ?? null,
		event_type: data.event_type ?? 'im.message.receive_v1',
		received_at: new Date().toISOString(),
		message_id: message.message_id ?? null,
		chat_id: message.chat_id ?? null,
		chat_type: message.chat_type ?? null,
		message_type: message.message_type ?? null,
		source: data.source ?? (data.event_type === 'manual.relay' ? 'manual-relay' : 'feishu'),
		source_label: data.source_label ?? null,
		text: payload.text,
		image_key: payload.resources.find((resource) => resource.resource_type === 'image')?.key ?? null,
		file_key: payload.resources.find((resource) => resource.resource_type === 'file')?.key ?? null,
		file_name: null,
		duration: null,
		sender_open_id: data.sender?.sender_id?.open_id ?? null,
		sender_type: data.sender?.sender_type ?? null,
		ingress_status: '已接收',
		n8n_status: '等待转发',
		target_status: null,
		target_batch_id: null,
		n8n_error: null,
		raw: data,
	};
}

function contentTypeFromBytes(bytes, fallback) {
	if (fallback && fallback !== 'application/octet-stream') return fallback;
	if (bytes.subarray(0, 3).equals(Buffer.from([0xff, 0xd8, 0xff]))) return 'image/jpeg';
	if (bytes.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) return 'image/png';
	if (bytes.subarray(0, 4).toString('ascii') === 'RIFF' && bytes.subarray(8, 12).toString('ascii') === 'WEBP') return 'image/webp';
	if (bytes.subarray(0, 3).toString('ascii') === 'ID3') return 'audio/mpeg';
	if (bytes.subarray(0, 4).toString('ascii') === 'RIFF' && bytes.subarray(8, 12).toString('ascii') === 'WAVE') return 'audio/wav';
	if (bytes.subarray(4, 8).toString('ascii') === 'ftyp') {
		if (bytes.subarray(8, 12).toString('ascii') === 'qt  ') return 'video/quicktime';
		const fallbackName = String(fallback ?? '').toLowerCase();
		if (fallbackName.includes('audio')) return 'audio/mp4';
		return 'video/mp4';
	}
	return fallback ?? 'application/octet-stream';
}

function extensionFor(mediaType) {
	return ({
		'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp',
		'audio/mp4': 'm4a', 'audio/x-m4a': 'm4a', 'audio/mpeg': 'mp3',
		'audio/wav': 'wav', 'audio/x-wav': 'wav', 'video/mp4': 'mp4', 'video/quicktime': 'mov',
	})[mediaType] ?? 'bin';
}

function filenameForMediaType(filename, mediaType) {
	const extension = extensionFor(mediaType);
	const safeName = String(filename ?? '').replace(/[^\w.\-()\u4e00-\u9fff]+/g, '_').slice(0, 255);
	const stem = safeName.replace(/\.[^.]*$/, '') || 'attachment';
	return `${stem}.${extension}`;
}

function partManifest(bytes, property) {
	const parts = [];
	for (let offset = 0; offset < bytes.length; offset += uploadPartBytes) {
		const part = bytes.subarray(offset, Math.min(offset + uploadPartBytes, bytes.length));
		const index = parts.length;
		parts.push({
			property: `${property}_part_${index}`,
			bytes: part.length,
			sha256: createHash('sha256').update(part).digest('hex'),
		});
	}
	return { part_size: uploadPartBytes, part_count: parts.length, parts };
}

async function persistReadableAsset(readable, property, fallbackName, fallbackType, lastModified) {
	const path = join(ingestionStorageDir, `${randomUUID()}-${property}.bin`);
	const writer = createWriteStream(path, { flags: 'wx' });
	const fullHash = createHash('sha256');
	const parts = [];
	let pending = Buffer.alloc(0);
	let total = 0;
	let firstBytes = Buffer.alloc(0);
	try {
		for await (const chunk of readable) {
			const bytes = Buffer.from(chunk);
			if (firstBytes.length < 16) firstBytes = Buffer.concat([firstBytes, bytes.subarray(0, 16 - firstBytes.length)]);
			fullHash.update(bytes); total += bytes.length;
			if (total > Number(process.env.INGESTION_MAX_FILE_BYTES ?? 524_288_000)) throw new Error('媒体超过 500 MB 上限');
			pending = pending.length ? Buffer.concat([pending, bytes]) : bytes;
			while (pending.length >= uploadPartBytes) {
				const part = pending.subarray(0, uploadPartBytes); pending = pending.subarray(uploadPartBytes);
				parts.push({ property: `${property}_part_${parts.length}`, bytes: part.length, sha256: createHash('sha256').update(part).digest('hex') });
			}
			await writeChunk(writer, bytes);
		}
		if (pending.length) parts.push({ property: `${property}_part_${parts.length}`, bytes: pending.length, sha256: createHash('sha256').update(pending).digest('hex') });
		await endWritable(writer);
		if (!total) throw new Error('飞书资源下载为空');
		const mediaType = contentTypeFromBytes(firstBytes, fallbackType);
		if (!supportedMediaTypes.has(mediaType)) throw new Error(`目标导入 API 不支持媒体类型：${mediaType}`);
		return { property, filename: filenameForMediaType(fallbackName, mediaType), media_type: mediaType, declared_bytes: total, content_sha256: fullHash.digest('hex'), part_size: uploadPartBytes, part_count: parts.length, parts, last_modified: lastModified, path };
	} catch (error) { writer.destroy(); await unlink(path).catch(() => {}); throw error; }
}

async function readAssetPart(resource, offset, bytes) {
	const file = await open(resource.path, 'r');
	try { const output = Buffer.allocUnsafe(bytes); const { bytesRead } = await file.read(output, 0, bytes, offset); return output.subarray(0, bytesRead); }
	finally { await file.close(); }
}

async function fetchWithBackoff(url, options, { maxAttempts = 4, baseDelayMs = 500 } = {}) {
	let lastError; let lastResponse;
	for (let attempt = 1; attempt <= maxAttempts; attempt++) {
		try {
			const response = await fetch(url, options);
			if (response.ok || (response.status >= 400 && response.status < 500)) return response;
			lastResponse = response;
			lastError = new Error(`HTTP ${response.status}`);
		} catch (error) { lastError = error; }
		if (attempt < maxAttempts) await new Promise((resolve) => setTimeout(resolve, baseDelayMs * 2 ** (attempt - 1) + Math.floor(Math.random() * 200)));
	}
	if (lastResponse) return lastResponse;
	throw lastError ?? new Error('request failed');
}

async function hydrateRemotePartState(resources) {
	if (!mediaStateWebhookUrl) return;
	for (const resource of resources) {
		if (!resource.remote_upload_id) continue;
		try {
			const response = await fetchWithBackoff(mediaStateWebhookUrl, {
				method: 'POST', headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ upload_id: resource.remote_upload_id }), signal: AbortSignal.timeout(130_000),
			}, { maxAttempts: 1 });
			if (!response.ok) continue;
			const state = await response.json();
			const received = state?.upload?.received_parts ?? state?.received_parts ?? state?.parts_received ?? [];
			if (!Array.isArray(received)) continue;
			const indexes = received.map(Number).filter((index) => Number.isInteger(index) && index >= 0);
			if (!indexes.length) continue;
			for (const part of resource.parts) if (indexes.includes(Number(part.part_index))) part.uploaded = true;
			if (resource.asset_id) await ledger.recordRemoteParts(resource.asset_id, indexes);
		} catch (error) {
			console.warn(`无法读取远端上传会话 ${resource.remote_upload_id}：${error instanceof Error ? error.message : String(error)}`);
		}
	}
}

function filenameFromHeaders(headers, fallback) {
	const value = headers?.['content-disposition'] ?? headers?.['Content-Disposition'];
	const match = typeof value === 'string' && value.match(/filename\*?=(?:UTF-8''|\")?([^;\"]+)/i);
	return match ? decodeURIComponent(match[1].replace(/\"/g, '')).replace(/[^\w.-]+/g, '_') : fallback;
}

async function downloadMedia(data, messageResourceApi = null) {
	const message = data.message ?? {};
	const { resources } = extractMessagePayload(message);
	if (!resources.length) return [];

	return Promise.all(resources.map(async (resource, index) => {
		let response;
		try {
			if (resource.resource_type === 'image' && resource.larkagentx_resource && larkAgentXResourceUrl && larkAgentXIngressToken) {
				const descriptor = resource.larkagentx_resource;
				const query = new URLSearchParams({ image_id: String(descriptor.image_id ?? resource.key), key_hex: String(descriptor.key_hex ?? ''), iv_hex: String(descriptor.iv_hex ?? '') });
				const bridgeResponse = await fetch(`${larkAgentXResourceUrl}?${query.toString()}`, { headers: { 'x-larkagentx-token': larkAgentXIngressToken } });
				if (!bridgeResponse.ok) throw new Error(`LarkAgentX 图片资源读取失败（HTTP ${bridgeResponse.status}）`);
				// The bridge serves the stored (AES-GCM encrypted) payload; decryption is the adapter's job.
				const bytes = decryptLarkAgentXImage(Buffer.from(await bridgeResponse.arrayBuffer()), descriptor);
				response = { headers: { 'content-type': bridgeResponse.headers.get('content-type') ?? 'application/octet-stream' }, getReadableStream: () => Readable.from(bytes) };
			} else if (messageResourceApi) {
				response = await messageResourceApi.messageResourceGet({ messageId: message.message_id, fileKey: resource.key, type: resource.resource_type });
			} else {
				// Official Feishu message-resource API. It authorizes against the app's
				// im:message:readonly (or broader) application scope.
				response = await larkClient.im.v1.messageResource.get({
					path: { message_id: message.message_id, file_key: resource.key },
					params: { type: resource.resource_type },
				});
			}
		} catch (error) {
			// An image inside a relayed card was uploaded by this app, so the
			// image endpoint can serve it even where the message-resource
			// endpoint will not address a card element.
			if (message.message_type === 'interactive' && resource.resource_type === 'image' && larkClient?.im?.v1?.image?.get) {
				try {
					response = await larkClient.im.v1.image.get({ path: { image_key: resource.key } });
				} catch (fallbackError) {
					throw new Error(`卡片图片读取失败：${fallbackError instanceof Error ? fallbackError.message : String(fallbackError)}`);
				}
			} else {
			if (messageResourceApi) throw new Error(`汇总群媒体读取失败：${error instanceof Error ? error.message : String(error)}`);
			if (error?.response?.status === 400) {
				throw new Error('飞书媒体下载被拒绝：请在开放平台申请并发布 im:message:readonly 权限');
			}
			throw new Error(`飞书媒体下载失败（HTTP ${error?.response?.status ?? '未知'}）`);
			}
		}
		const headerType = String(response.headers?.['content-type'] ?? '').split(';')[0];
		const fallbackName = filenameFromHeaders(response.headers, `${resource.resource_type}-${resource.key}.bin`);
		return persistReadableAsset(response.getReadableStream(), `media_${index}`, fallbackName, headerType, Number(message.create_time ?? Date.now()));
	}));
}

function manualResource(file, index) {
	if (!file || typeof file !== 'object' || typeof file.data_base64 !== 'string') {
		throw new Error('手动投递的媒体格式无效');
	}
	const bytes = Buffer.from(file.data_base64, 'base64');
	if (!bytes.length || bytes.length > 12 * 1024 * 1024) throw new Error('单个媒体应介于 1 B 和 12 MB 之间');
	const mediaType = contentTypeFromBytes(bytes, String(file.media_type ?? '').split(';')[0]);
	if (!supportedMediaTypes.has(mediaType)) throw new Error(`不支持的手动投递媒体类型：${mediaType}`);
	const fallbackName = `manual-${index + 1}.${extensionFor(mediaType)}`;
	const filename = filenameForMediaType(String(file.filename ?? fallbackName), mediaType);
	return {
		property: `media_${index}`,
		filename,
		media_type: mediaType,
		declared_bytes: bytes.length,
		content_sha256: createHash('sha256').update(bytes).digest('hex'),
		...partManifest(bytes, `media_${index}`),
		last_modified: Date.now(),
		data: bytes,
	};
}

function sendSse(response, event, payload) {
	response.write(`event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`);
}

function broadcastSnapshot() {
	for (const response of eventStreams) sendSse(response, 'snapshot', recentEvents);
}

function addEvent(data) {
	const event = summarizeEvent(data);
	recentEvents.unshift(event);
	if (recentEvents.length > maxRecentEvents) recentEvents.pop();
	for (const response of eventStreams) sendSse(response, 'message', event);
	return event;
}

function updateEvent(eventId, patch) {
	const event = recentEvents.find((entry) => entry.event_id === eventId);
	if (!event) return;
	Object.assign(event, patch);
	broadcastSnapshot();
}

function summarizeN8nResult(payload) {
	const batch = payload?.batch ?? null;
	if (!batch) return { target_status: 'n8n 已完成' };
	const percent = Number.isFinite(batch.percentage) ? `（${batch.percentage}%）` : '';
	return {
		target_batch_id: batch.id ?? null,
		target_status: `已提交至目标服务：${batch.state ?? 'unknown'}${percent}`,
	};
}

const dashboardHtml = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Feishu Bot Monitor</title>
  <style>
    :root { color-scheme: dark; font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0b1020; color: #edf2ff; }
    body { margin: 0; } main { max-width: 1080px; margin: 0 auto; padding: 36px 24px 64px; }
    header { display: flex; align-items: end; justify-content: space-between; gap: 20px; margin-bottom: 24px; }
    h1 { margin: 0; font-size: 28px; } p { color: #aab7d4; margin: 8px 0 0; }
    #status { color: #65e6a6; font-size: 14px; white-space: nowrap; } #count { color: #aab7d4; font-size: 14px; margin: 16px 0; }
    #events { display: grid; gap: 14px; } article { background: #131b31; border: 1px solid #263452; border-radius: 12px; padding: 18px; }
    .top { display: flex; justify-content: space-between; gap: 16px; } .kind { color: #79aaff; font-weight: 700; }
    .states { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0 2px; } .state { border-radius: 999px; padding: 4px 9px; font-size: 13px; background: #203157; color: #c7d8ff; } .state.ok { background: #173f32; color: #82efb7; } .state.fail { background: #542738; color: #ffb6c5; }
    time { color: #8fa0c3; font-size: 13px; } .text { white-space: pre-wrap; line-height: 1.55; margin: 14px 0; }
    dl { display: grid; grid-template-columns: 140px 1fr; gap: 7px 14px; margin: 14px 0 0; font-size: 14px; }
    dt { color: #8fa0c3; } dd { margin: 0; overflow-wrap: anywhere; } details { margin-top: 14px; } pre { max-height: 360px; overflow: auto; background: #0b1020; border-radius: 8px; padding: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
    .empty { border: 1px dashed #455474; border-radius: 12px; padding: 32px; text-align: center; color: #8fa0c3; }
  </style>
</head>
<body><main>
  <header><div><h1>飞书机器人消息监控</h1><p>本机实时视图，保留当前适配器进程接收的最近 200 条事件。</p></div><div id="status">连接中…</div></header>
  <div id="count"></div><section id="events"><div class="empty">等待飞书消息…</div></section>
</main>
<script>
  const events = document.querySelector('#events');
  const count = document.querySelector('#count');
  const status = document.querySelector('#status');
  let total = 0;
  const metadata = [['event_type','事件'],['source','本机入口'],['source_label','来源备注'],['message_id','消息 ID'],['chat_id','群 ID'],['chat_type','会话类型'],['sender_open_id','发送者 Open ID'],['sender_type','发送者类型'],['target_batch_id','目标批次 ID'],['n8n_error','n8n 错误'],['image_key','图片 Key'],['file_key','文件 Key'],['file_name','文件名'],['duration','时长(ms)']];
  function field(parent, label, value) { if (value === null || value === undefined) return; const dt=document.createElement('dt'); dt.textContent=label; const dd=document.createElement('dd'); dd.textContent=value; parent.append(dt,dd); }
  function state(text, kind='') { const badge=document.createElement('span'); badge.className='state '+kind; badge.textContent=text; return badge; }
  function card(entry, prepend=false) {
    const article=document.createElement('article'); const top=document.createElement('div'); top.className='top';
    const kind=document.createElement('div'); kind.className='kind'; kind.textContent=entry.message_type || entry.event_type;
    const time=document.createElement('time'); time.textContent=new Date(entry.received_at).toLocaleString(); top.append(kind,time); article.append(top);
    const states=document.createElement('div'); states.className='states';
    states.append(state('飞书：'+(entry.ingress_status || '已接收'),'ok'));
    const n8nKind=entry.n8n_status === '已完成' ? 'ok' : entry.n8n_status === '失败' ? 'fail' : '';
    states.append(state('n8n：'+(entry.n8n_status || '未知'),n8nKind));
    if (entry.target_status) states.append(state('目标：'+entry.target_status, entry.n8n_status === '已完成' ? 'ok' : ''));
    article.append(states);
    if (entry.text) { const text=document.createElement('div'); text.className='text'; text.textContent=entry.text; article.append(text); }
    const dl=document.createElement('dl'); metadata.forEach(([key,label])=>field(dl,label,entry[key])); article.append(dl);
    const details=document.createElement('details'); const summary=document.createElement('summary'); summary.textContent='查看原始事件 JSON'; const pre=document.createElement('pre'); pre.textContent=JSON.stringify(entry.raw,null,2); details.append(summary,pre); article.append(details);
    const empty=events.querySelector('.empty'); if(empty) empty.remove(); if(prepend) events.prepend(article); else events.append(article);
  }
  function refreshCount(){ count.textContent='当前会话已接收 '+total+' 条事件'; }
  const source=new EventSource('/events');
  source.addEventListener('snapshot', e=>{ const data=JSON.parse(e.data); events.replaceChildren(); total=data.length; data.forEach(item=>card(item)); if (!data.length) events.innerHTML='<div class="empty">等待飞书消息…</div>'; refreshCount(); });
  source.addEventListener('message', e=>{ card(JSON.parse(e.data),true); total++; refreshCount(); });
  source.onopen=()=>status.textContent='实时连接已建立';
  source.onerror=()=>status.textContent='连接断开，正在重试…';
</script></body></html>`;

const relayHtml = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />
<title>本机消息投递台</title><style>
  :root{color-scheme:dark;font-family:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0b1020;color:#edf2ff}body{margin:0}main{max-width:820px;margin:0 auto;padding:36px 24px 64px}header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:26px}h1{margin:0;font-size:29px}p{color:#aab7d4;line-height:1.6}a{color:#8eb3ff}form{background:#131b31;border:1px solid #263452;border-radius:14px;padding:22px;display:grid;gap:18px}label{display:grid;gap:8px;color:#cbd7f2;font-size:14px}select,input,textarea,button{font:inherit;border-radius:9px;border:1px solid #405276;background:#0b1020;color:#edf2ff;padding:10px 12px}textarea{min-height:190px;resize:vertical;line-height:1.55}.row{display:grid;grid-template-columns:1fr 1fr;gap:14px}.drop{border:1px dashed #5272ad;border-radius:10px;padding:20px;text-align:center;color:#aab7d4}.drop.drag{background:#17274a;border-color:#8eb3ff}.files{margin:0;padding-left:20px;color:#cbd7f2;font-size:14px}.files:empty{display:none}button{cursor:pointer;background:#3268c6;border:0;font-weight:700}button:disabled{opacity:.65;cursor:wait}#result{min-height:22px;color:#aab7d4}.ok{color:#82efb7}.bad{color:#ffb6c5}.hint{font-size:13px;color:#8fa0c3;margin:0}.tag{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
</style></head><body><main>
<header><div><h1>本机消息投递台</h1><p>从飞书、微信或网页复制文字；拖入或粘贴图片/音频后直接进入市场复盘工作流。</p></div><a href="/">查看处理监控</a></header>
<form id="relay"><div class="row"><label>路由标签<select id="tag">${RELAY_ROUTE_OPTIONS_SLOT}</select></label><label>来源备注（可选）<input id="source" maxlength="120" placeholder="如：微信个人群 / 飞书群 A" /></label></div>
<div class="row"><label>指定日期（可选）<input id="date" type="date" /></label><label>指定时间（可选）<input id="time" type="time" /></label></div>
<label>正文<textarea id="text" placeholder="粘贴消息正文。若不指定日期和时间，按本机收到内容时的北京时间记录。"></textarea><button id="fillClipboard" type="button">填入当前剪贴板文字</button></label>
<div id="drop" class="drop" tabindex="0">拖入图片、音频或视频，或在此页面直接粘贴媒体。<br /><span class="hint">支持多文件；单个媒体受本地入口大小限制。</span><br /><input id="file" type="file" accept="image/jpeg,image/png,image/webp,audio/mp4,audio/x-m4a,audio/mpeg,audio/wav,audio/x-wav,video/mp4,video/quicktime" multiple /></div><ul id="files" class="files"></ul>
<button id="submit" type="submit">投递到市场复盘</button><div id="result" role="status"></div>
</form></main><script>
const staged=[]; const files=document.querySelector('#files'); const drop=document.querySelector('#drop'); const result=document.querySelector('#result'); const submit=document.querySelector('#submit');
async function fillClipboard(){try{const text=await navigator.clipboard.readText();if(!text.trim())throw new Error('剪贴板没有文字');document.querySelector('#text').value=text;result.className='ok';result.textContent='已填入剪贴板文字。';}catch(err){result.className='bad';result.textContent='无法读取剪贴板：'+(err.message||err);}}
document.querySelector('#fillClipboard').addEventListener('click',fillClipboard);
const draftId=new URLSearchParams(location.search).get('draft');if(draftId){fetch('/relay-draft/'+encodeURIComponent(draftId),{cache:'no-store'}).then(r=>r.json().then(b=>({r,b}))).then(({r,b})=>{if(!r.ok)throw new Error(b.message);document.querySelector('#text').value=b.text;result.className='ok';result.textContent='已从快捷键填入剪贴板文字。';}).catch(err=>{result.className='bad';result.textContent=err.message||String(err);});}
function render(){files.replaceChildren(...staged.map((f,i)=>{const li=document.createElement('li');li.textContent=(i+1)+'. '+f.name+' · '+Math.ceil(f.size/1024)+' KB';return li;}));}
function add(list){for(const f of list){if(!staged.some(x=>x.name===f.name&&x.size===f.size&&x.lastModified===f.lastModified)) staged.push(f);}render();}
document.querySelector('#file').addEventListener('change',e=>add(e.target.files));
for(const type of ['dragenter','dragover']) drop.addEventListener(type,e=>{e.preventDefault();drop.classList.add('drag')});
for(const type of ['dragleave','drop']) drop.addEventListener(type,e=>{e.preventDefault();drop.classList.remove('drag')});
drop.addEventListener('drop',e=>add(e.dataTransfer.files));
window.addEventListener('paste',e=>{const pasted=[...e.clipboardData.items].filter(x=>x.kind==='file').map(x=>x.getAsFile()).filter(Boolean);if(pasted.length){e.preventDefault();add(pasted);result.textContent='已加入 '+pasted.length+' 个剪贴板媒体。';}});
document.querySelector('#relay').addEventListener('submit',async e=>{e.preventDefault();result.className='';const tag=document.querySelector('#tag').value;const date=document.querySelector('#date').value;const time=document.querySelector('#time').value;if((date&&!time)||(!date&&time)){result.className='bad';result.textContent='指定时间时请同时填写日期和时间。';return;}const text=document.querySelector('#text').value.trim();if(!text&&!staged.length){result.className='bad';result.textContent='请至少填写正文或加入一个媒体。';return;}submit.disabled=true;submit.textContent='投递中…';try{const form=new FormData();form.append('tag',tag);form.append('text',text);form.append('source_label',document.querySelector('#source').value.trim());if(date)form.append('content_date',date);if(time)form.append('content_time',time);for(const file of staged)form.append('media',file,file.name);const response=await fetch('/manual-relay',{method:'POST',body:form});const body=await response.json();if(!response.ok)throw new Error(body.message||'投递失败');result.className='ok';result.textContent='已接收：'+body.message_id+'；n8n 正在处理。';document.querySelector('#text').value='';document.querySelector('#file').value='';staged.splice(0);render();}catch(err){result.className='bad';result.textContent=err.message||String(err);}finally{submit.disabled=false;submit.textContent='投递到市场复盘';}});
</script></body></html>`;

function readJsonBody(request, limit = 18 * 1024 * 1024) {
	return new Promise((resolve, reject) => {
		const chunks = [];
		let size = 0;
		request.on('data', (chunk) => {
			size += chunk.length;
			if (size > limit) {
				reject(new Error('投递内容过大，媒体总大小请控制在 12 MB 以内'));
				request.destroy();
				return;
			}
			chunks.push(chunk);
		});
		request.on('end', async () => {
			try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
			catch { reject(new Error('请求不是有效 JSON')); }
		});
		request.on('error', reject);
	});
}

async function handleWeChatGroupRelay(request, response) {
	if (!wechatGroupRelayConfig.endpointToken || request.headers['x-wechat-relay-token'] !== wechatGroupRelayConfig.endpointToken) {
		response.writeHead(401, { 'content-type': 'application/json' });
		response.end(JSON.stringify({ status: 'unauthorized' }));
		return;
	}
	if (!wechatGroupRelay) {
		response.writeHead(503, { 'content-type': 'application/json' });
		response.end(JSON.stringify({ status: 'disabled' }));
		return;
	}
	try {
		const payload = await readJsonBody(request, 16 * 1024 * 1024);
		const result = await wechatGroupRelay.process(payload);
		response.writeHead(result.status === 'sent' ? 201 : 200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify(result));
	} catch (error) {
		console.error(`微信群 relay 请求失败：${error instanceof Error ? error.message : String(error)}`);
		response.writeHead(400, { 'content-type': 'application/json' });
		response.end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) }));
	}
}

async function handleQuantAlert(request, response) {
	if (!quantAlertWebhookToken || request.headers['x-quant-alert-token'] !== quantAlertWebhookToken) {
		response.writeHead(401, { 'content-type': 'application/json' });
		response.end(JSON.stringify({ status: 'unauthorized' }));
		return;
	}
	if (!feishuAlertReceiveId) {
		response.writeHead(503, { 'content-type': 'application/json' });
		response.end(JSON.stringify({ status: 'disabled', reason: 'FEISHU_ALERT_RECEIVE_ID is not configured' }));
		return;
	}
	try {
		const payload = await readJsonBody(request, 16 * 1024);
		const text = String(payload?.text ?? '').trim();
		if (!text) throw new Error('alert text is required');
		if (text.length > 3500) throw new Error('alert text exceeds 3500 characters');
		// The quant outbox's delivery id arrives as idempotency_key; Feishu drops a
		// second create with the same uuid, so a retry after a lost response
		// cannot post the alert twice.
		const idempotencyKey = String(payload?.idempotency_key ?? '').trim().slice(0, 50);
		const result = await larkClient.im.v1.message.create({
			params: { receive_id_type: feishuAlertReceiveIdType },
			data: { receive_id: feishuAlertReceiveId, msg_type: 'text', content: JSON.stringify({ text }), ...(idempotencyKey ? { uuid: idempotencyKey } : {}) },
		});
		response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'sent', message_id: result?.data?.message_id ?? null }));
	} catch (error) {
		console.error(`盘中提醒投递失败：${error instanceof Error ? error.message : String(error)}`);
		response.writeHead(502, { 'content-type': 'application/json' });
		response.end(JSON.stringify({ status: 'failed', message: 'Feishu alert delivery failed' }));
	}
}

async function handlePaperKbAlert(request, response) {
	if (!paperKbAlertWebhookToken || request.headers['x-paper-kb-token'] !== paperKbAlertWebhookToken) {
		response.writeHead(401, { 'content-type': 'application/json' });
		response.end(JSON.stringify({ status: 'unauthorized' }));
		return;
	}
	if (!/^oc_[A-Za-z0-9]+$/.test(paperKbAlertReceiveId)) {
		response.writeHead(503, { 'content-type': 'application/json' });
		response.end(JSON.stringify({ status: 'disabled', reason: 'PAPER_KB_FEISHU_CHAT_ID is not a verified chat id' }));
		return;
	}
	try {
		let payload;
		try { payload = await readJsonBody(request, 16 * 1024); }
		catch (error) {
			response.writeHead(400, { 'content-type': 'application/json' });
			response.end(JSON.stringify({ status: 'invalid_request', message: error instanceof Error ? error.message : String(error) }));
			return;
		}
		const text = String(payload?.text ?? '').trim();
		const idempotencyKey = String(payload?.idempotency_key ?? '').trim();
		const targetChatId = String(payload?.chat_id ?? paperKbAlertReceiveId).trim();
		if (!text) { response.writeHead(400, { 'content-type': 'application/json' }); response.end(JSON.stringify({ status: 'invalid_request', message: 'paper-kb text is required' })); return; }
		if (text.length > 3500) { response.writeHead(400, { 'content-type': 'application/json' }); response.end(JSON.stringify({ status: 'invalid_request', message: 'paper-kb text exceeds 3500 characters' })); return; }
		if (!/^[A-Za-z0-9:_-]{8,200}$/.test(idempotencyKey)) { response.writeHead(400, { 'content-type': 'application/json' }); response.end(JSON.stringify({ status: 'invalid_request', message: 'paper-kb idempotency_key is required' })); return; }
		if (targetChatId !== paperKbAlertReceiveId) { response.writeHead(403, { 'content-type': 'application/json' }); response.end(JSON.stringify({ status: 'forbidden', message: 'paper-kb target chat is not allowlisted' })); return; }
		const contentSha256 = createHash('sha256').update(text, 'utf8').digest('hex');
		if (payload?.content_sha256 && String(payload.content_sha256) !== contentSha256) { response.writeHead(400, { 'content-type': 'application/json' }); response.end(JSON.stringify({ status: 'invalid_request', message: 'paper-kb content_sha256 mismatch' })); return; }
		const previous = paperKbQueues.get(targetChatId) ?? Promise.resolve();
		const current = previous.catch(() => {}).then(async () => {
			let claimed = false;
			const claim = await ledger.claimPaperKbDelivery({ eventId: idempotencyKey, targetChatId, contentSha256, owner: `paper-kb-${process.pid}` });
			if (claim.conflict) return { status: 'conflict', chat_id: targetChatId, event_id: idempotencyKey, error: 'idempotency key is already bound to different content or target' };
			if (claim.duplicate) return { status: 'duplicate', chat_id: targetChatId, message_id: claim.row.message_id, event_id: idempotencyKey, verification: { feishu: 'receipt', archive: claim.row.archive_status, downstream: claim.row.downstream_status } };
			if (claim.blocked) return { status: 'blocked', chat_id: targetChatId, event_id: idempotencyKey, error: claim.row.last_error };
			if (claim.inFlight) return { status: 'in-flight', chat_id: targetChatId, event_id: idempotencyKey };
			claimed = true;
			try {
				const result = await retryDelivery(() => larkClient.im.v1.message.create({
					params: { receive_id_type: 'chat_id' },
					data: { receive_id: targetChatId, msg_type: 'text', content: JSON.stringify({ text }), uuid: paperKbFeishuUuid(idempotencyKey) },
				}), { maxAttempts: 3 });
				const messageId = result?.data?.message_id ?? null;
				if (!messageId) throw new Error('Feishu did not return message_id');
				await ledger.completePaperKbDelivery(idempotencyKey, {
					status: 'sent', messageId,
					remoteReceipt: { message_id: messageId, chat_id: targetChatId, received_at: new Date().toISOString() },
				});
				return { status: 'sent', chat_id: targetChatId, message_id: messageId, event_id: idempotencyKey, verification: { feishu: 'receipt', archive: 'not_applicable', downstream: 'not_applicable' } };
			} catch (error) {
				if (claimed) await ledger.completePaperKbDelivery(idempotencyKey, { status: 'failed', errorMessage: safeDeliveryError(error) });
				throw error;
			}
		});
		paperKbQueues.set(targetChatId, current);
		try {
			const result = await current;
			response.writeHead(result.status === 'failed' ? 502 : result.status === 'conflict' ? 409 : result.status === 'in-flight' ? 202 : 200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify(result));
		} finally {
			if (paperKbQueues.get(targetChatId) === current) paperKbQueues.delete(targetChatId);
		}
	} catch (error) {
		const detail = safeDeliveryError(error);
		console.error(`Paper-KB 学习提醒投递失败：${detail}`);
		response.writeHead(502, { 'content-type': 'application/json' });
		response.end(JSON.stringify({ status: 'failed', message: 'Paper-KB Feishu delivery failed', detail }));
	}
}

async function handleFeishuUserOauth(request, response) {
	if (!quantWriteApiKey || request.headers['x-quant-write-key'] !== quantWriteApiKey) {
		response.writeHead(401, { 'content-type': 'application/json' });
		response.end(JSON.stringify({ status: 'unauthorized' }));
		return;
	}
	try {
		const payload = await readJsonBody(request, 16 * 1024);
		const token = payload.force_refresh
			? await feishuUserOauth.forceRefresh()
			: payload.refresh_token
				? await feishuUserOauth.bootstrapRefreshToken(payload.refresh_token)
				: await feishuUserOauth.exchangeAuthorizationCode(payload.authorization_code, payload.redirect_uri);
		response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'stored', ...token }));
	} catch (error) {
		console.error(`飞书用户 OAuth 授权失败：${error instanceof Error ? error.message : String(error)}`);
		response.writeHead(400, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) }));
	}
}

function readMultipartBody(request) {
	return new Promise((resolve, reject) => {
		let parser;
		try { parser = Busboy({ headers: request.headers, limits: { files: 12, fileSize: Number(process.env.INGESTION_MAX_FILE_BYTES ?? 524_288_000), fields: 8 } }); }
		catch (error) { reject(error); return; }
		const fields = {};
		const resources = [];
		const pending = [];
		parser.on('field', (name, value) => { fields[name] = value; });
		parser.on('file', (name, stream, info) => {
			const index = resources.length;
			const task = persistReadableAsset(stream, `manual_${index}`, String(info.filename || `manual-${index + 1}.bin`), String(info.mimeType || 'application/octet-stream'), Date.now()).then((asset) => { resources[index] = { ...asset, filename: asset.filename.replace(/[^\w.\-()\u4e00-\u9fff]+/g, '_').slice(0, 255) }; });
			pending.push(task);
		});
		parser.on('filesLimit', () => reject(new Error('一次最多上传 12 个文件')));
		parser.on('error', reject);
		parser.on('finish', async () => { try { await Promise.all(pending); resolve({ fields, resources }); } catch (error) { reject(error); } });
		request.pipe(parser);
	});
}

function createRelayDraft(text) {
	const now = Date.now();
	for (const [id, draft] of relayDrafts) if (draft.expires_at <= now) relayDrafts.delete(id);
	const id = randomUUID();
	relayDrafts.set(id, { text, expires_at: now + 5 * 60 * 1000 });
	return id;
}

async function buildManualEvent(input) {
	const tag = String(input?.tag ?? '').toLowerCase();
	resolveRoute(tag);
	const text = String(input?.text ?? '').trim();
	const contentDate = input?.content_date ? String(input.content_date) : '';
	const contentTime = input?.content_time ? String(input.content_time) : '';
	if ((contentDate && !contentTime) || (!contentDate && contentTime) || (contentDate && !isValidDateTime(contentDate, contentTime))) {
		throw new Error('指定时间无效，请同时填写日期和时间');
	}
	const resources = Array.isArray(input?.resources) ? input.resources : (Array.isArray(input?.media) ? input.media.map(manualResource) : []);
	if (!text && !resources.length) throw new Error('请至少提供正文或一个媒体');
	if (resources.length > 12) throw new Error('一次最多投递 12 个媒体');
	const eventId = String(input?.event_id ?? '').trim() || `manual-${randomUUID()}`;
	const messageId = String(input?.message_id ?? '').trim() || `manual_${randomUUID().replace(/-/g, '')}`;
	const timestamp = contentDate ? `@${contentDate} ${contentTime}` : '';
	const messageText = [`#${tag}`, timestamp, text].filter(Boolean).join('\n');
	const content = {
		title: '',
		content_v2: [
			[{ tag: 'text', text: messageText, style: [] }],
			...resources.map((resource, index) => [resource.media_type.startsWith('image/')
				? { tag: 'img', image_key: `manual-image-${index + 1}` }
				: { tag: 'media', file_key: `manual-media-${index + 1}` }]),
		],
	};
	return {
		data: {
			event_id: eventId,
			event_type: 'manual.relay',
			source: input?.ingress_source ? String(input.ingress_source).slice(0, 80) : 'manual-relay',
			source_label: input?.source_label ? String(input.source_label).slice(0, 120) : null,
			message: {
				message_id: messageId, chat_id: 'local-manual-relay', chat_type: 'local', message_type: 'post',
				create_time: String(Date.now()), content: JSON.stringify(content),
			},
			sender: { sender_id: { open_id: 'local-manual-relay' }, sender_type: 'user' },
		},
		resources,
		messageText,
	};
}

async function handleManualRelay(request, response) {
	let manual = null;
	try {
		const multipart = String(request.headers['content-type'] ?? '').toLowerCase().startsWith('multipart/form-data');
		const parsed = multipart ? await readMultipartBody(request) : await readJsonBody(request);
		const input = multipart ? { ...parsed.fields, resources: parsed.resources } : parsed;
		manual = await buildManualEvent(input);
		addEvent(manual.data);
		updateEvent(manual.data.event_id, { n8n_status: manual.resources.length ? '上传媒体并转发中' : '转发中' });
		const result = await forwardToN8n(manual.data, { resources: manual.resources, messageText: manual.messageText, sourceLabel: input.source_label });
		updateEvent(manual.data.event_id, { n8n_status: result?.duplicate ? '重复已跳过' : '已接收，处理中', target_status: result?.duplicate ? '本地幂等去重，未重复请求远端' : null });
		response.writeHead(202, { 'content-type': 'application/json' });
		response.end(JSON.stringify({ status: 'accepted', message_id: manual.data.message.message_id }));
	} catch (error) {
		if (manual?.resources) await Promise.all(manual.resources.map((resource) => resource.path ? unlink(resource.path).catch(() => {}) : Promise.resolve()));
		const message = error instanceof Error ? error.message : String(error);
		response.writeHead(400, { 'content-type': 'application/json' });
		response.end(JSON.stringify({ status: 'error', message }));
	}
}

async function handleLarkAgentXInbound(request, response) {
	if (!larkAgentXIngressToken || request.headers['x-larkagentx-token'] !== larkAgentXIngressToken) {
		response.writeHead(401, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'unauthorized' }));
		return;
	}
	try {
		const input = await readJsonBody(request, 64 * 1024);
		const data = normalizeLarkAgentXMessage(input);
		// Keep the private WebSocket ingress on the same event path as the
		// official Feishu dispatcher.  In particular, paper commands (收/收录/
		// 查询) are adapter commands and must not be mistaken for ordinary
		// research text just because they arrived through LarkAgentX.
		const result = await dispatchFeishuMessage(data, {
			source: 'larkagentx', sourceLabel: data.source_label,
			messageText: JSON.parse(data.message.content).text,
			receivedAt: new Date().toISOString(),
		});
		response.writeHead(202, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: result?.duplicate ? 'duplicate' : 'accepted', message_id: data.message.message_id, job_id: result?.jobId ?? null }));
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		response.writeHead(400, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'error', message }));
	}
}

// The bridge's own list of summary chats, cached briefly.  An unreachable
// bridge yields an empty list, so a numeric id is refused and the bridge
// retries the event from its spool.
let bridgeSummaryCache = { at: 0, ids: [] };
async function bridgeSummaryChatIds() {
	if (Date.now() - bridgeSummaryCache.at < 60_000) return bridgeSummaryCache.ids;
	const status = await larkAgentXDashboardStatus().catch(() => null);
	const ids = Array.isArray(status?.summary_chat_ids) ? status.summary_chat_ids.map((value) => String(value)) : [];
	if (status?.status !== 'unavailable') bridgeSummaryCache = { at: Date.now(), ids };
	return ids;
}

async function handleLarkAgentXSummaryInbound(request, response) {
	if (!larkAgentXIngressToken || request.headers['x-larkagentx-token'] !== larkAgentXIngressToken) {
		response.writeHead(401, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'unauthorized' }));
		return;
	}
	try {
		const input = await readJsonBody(request, 64 * 1024);
		if (!isSummaryChat({ chatId: input?.chat_id, configuredChatId: summaryListenerConfig.chatId, bridgeSummaryChatIds: await bridgeSummaryChatIds() })) throw new Error('LarkAgentX 汇总群 chat_id 不匹配');
		const data = normalizeLarkAgentXSummaryMessage(input);
		addEvent(data);
		updateEvent(data.event_id, { n8n_status: 'LarkAgentX 汇总群消息转发中' });
		const result = await processSummaryGroupMessage(data, { messageResourceApi: null, source: 'larkagentx-summary' });
		updateEvent(data.event_id, { n8n_status: result?.duplicate ? '重复已跳过' : '已接收，处理中', target_status: result?.duplicate ? '本地幂等去重，未重复请求远端' : null });
		response.writeHead(result?.ignored ? 202 : 201, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: result?.ignored ? 'ignored' : result?.duplicate ? 'duplicate' : 'accepted', event_id: data.event_id, message_id: data.message.message_id, job_id: result?.jobId ?? null }));
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		console.error(`LarkAgentX 汇总群 relay 失败：${message}`);
		response.writeHead(400, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'error', message }));
	}
}

async function handleLarkAgentXRouteCatalog(request, response) {
	if (!larkAgentXIngressToken || request.headers['x-larkagentx-token'] !== larkAgentXIngressToken) {
		response.writeHead(401, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'unauthorized' }));
		return;
	}
	try {
		const routes = await ledger.relayRoutes();
		response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ routes: routes.map((route) => ({ source_key: route.key, source_chat_id: route.chatId, chat_name: route.chatName, tag: route.tag, enabled: route.enabled !== false })) }));
	} catch (error) {
		response.writeHead(503, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) }));
	}
}

async function handleLarkAgentXGroupRelayInbound(request, response) {
	if (!larkAgentXIngressToken || request.headers['x-larkagentx-token'] !== larkAgentXIngressToken) {
		response.writeHead(401, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'unauthorized' }));
		return;
	}
	if (!larkAgentXGroupRelayEnabled) {
		response.writeHead(503, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'disabled' }));
		return;
	}
	try {
		const input = await readJsonBody(request, 64 * 1024);
		const chatId = String(input?.chat_id ?? '').trim();
		const dynamicRoute = input?._larkagentx_route && typeof input._larkagentx_route === 'object' ? input._larkagentx_route : null;
		const declaredSourceKey = String(dynamicRoute?.source_key ?? '').trim();
		const { sourceKey, source, staleConfiguredKey } = resolveLarkAgentXSource({
			chatId, configuredKey: larkAgentXSourceKeyForChat(chatId) ?? '', declaredRoute: dynamicRoute, routes: await ledger.relayRoutes(),
		});
		if (staleConfiguredKey) {
			if (!larkAgentXStaleStaticChats.has(chatId)) console.warn(`LARKX_GROUP_RELAY_ROUTES 中 ${chatId}=${staleConfiguredKey} 已过期，按 bridge 校验过的绑定改用 ${sourceKey}；请修正该 env 条目`);
			larkAgentXStaleStaticChats.add(chatId);
			larkAgentXDynamicGroupRelayRoutes.set(chatId, sourceKey);
		} else if (declaredSourceKey && !larkAgentXGroupRelayRoutes.has(chatId)) larkAgentXDynamicGroupRelayRoutes.set(chatId, sourceKey);
		if (source.enabled === false) {
			response.writeHead(202, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({ status: 'filtered', reason: '实时源路由已停用', source_key: sourceKey, chat_id: chatId }));
			return;
		}
		const targetChatIds = [...new Set([groupRelayConfig.targetChatId, ...(source.targetChatIds ?? [])].map((value) => String(value ?? '').trim()).filter(Boolean))];
		if (!targetChatIds.length) throw new Error(`实时源路由没有目标群：${sourceKey}`);
		const cardNeedsBackfill = ['CARD', 'INTERACTIVE'].includes(larkAgentXMessageType(input)) && !hasLarkAgentXCardPayload(input);
		const sourceTag = String(source.tag ?? '').trim().toLowerCase();
		const cardBackfillScoped = larkAgentXCardBackfillEnabled && (!larkAgentXCardBackfillTags.size || larkAgentXCardBackfillTags.has(sourceTag) || larkAgentXCardBackfillTags.has(String(source.key ?? '').trim().toLowerCase()));
		const allowOfficialFallback = larkAgentXOfficialFallbackEnabled || (cardBackfillScoped && cardNeedsBackfill);
		let message;
		if (isDirectLarkAgentXRelayType(input)) {
			message = normalizeLarkAgentXRelayMessage(input);
		} else if (!allowOfficialFallback) {
			message = normalizeLarkAgentXUnsupportedMessage(input);
		} else {
			try {
				// LarkAgentX's personal WebSocket uses its numeric chat id, while
				// the user OAuth message API resolves the same group by its official
				// oc_ chat id retained in the relay route.
				message = await readLarkAgentXBackfill(input, source, { sourceApi: feishuUserOauth.sourceApi });
			} catch (error) {
				// A card with no protobuf body must not disappear merely because the
				// optional official backfill lane is quota-limited or temporarily
				// unavailable. Preserve the real-time event as a durable placeholder;
				// the scoped repair endpoint can upgrade it when the official lane is
				// available again.
				if (cardBackfillScoped && cardNeedsBackfill) {
					console.warn(`LarkAgentX 卡片补读不可用，保留实时占位：${source.key} ${error instanceof Error ? error.message : String(error)}`);
					message = normalizeLarkAgentXUnsupportedMessage(input);
				} else {
					throw error;
				}
			}
		}
		const resolvedChatId = message.oauth_chat_id || chatId;
		const result = await groupRelay.processInbound({ ...message, source_chat_id: resolvedChatId }, { ...source, resolvedChatId, targetChatIds, targetChatId: targetChatIds[0] }, {
			replacePlaceholder: input?._larkagentx_force_placeholder === true,
			correction: input?._larkagentx_correction === true,
		});
		response.writeHead(result.status === 'sent' ? 201 : 202, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ ...result, source_key: sourceKey, chat_id: chatId }));
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		console.error(`LarkAgentX 群 relay 失败：${message}`);
		response.writeHead(Number(error?.statusCode) || 400, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'error', message }));
	}
}

async function handleLarkAgentXGapRepair(request, response) {
	if (!larkAgentXIngressToken || request.headers['x-larkagentx-token'] !== larkAgentXIngressToken) {
		response.writeHead(401, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'unauthorized' }));
		return;
	}
	try {
		const input = await readJsonBody(request, 16 * 1024);
		const explicitSourceKeys = Array.isArray(input?.source_keys)
			? input.source_keys.map((value) => String(value ?? '').trim()).filter(Boolean)
			: [];
		const sourceKeys = explicitSourceKeys.length
			? explicitSourceKeys
			: (Array.isArray(input?.source_chat_ids) ? input.source_chat_ids
			.map((value) => larkAgentXSourceKeyForChat(String(value ?? '').trim()))
				.filter(Boolean) : []);
		if (!sourceKeys.length) throw new Error('缺口补读必须指定 source_keys 或已映射的 source_chat_ids');
		const result = await groupRelay.repairFromOfficial({ fromCreateTime: input?.from_time, toCreateTime: input?.to_time, sourceKeys, forcePlaceholderCards: input?.force_placeholder_cards === true });
		response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'completed', ...result }));
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		console.error(`LarkAgentX 缺口补读失败：${message}`);
		response.writeHead(400, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'error', message }));
	}
}

async function handleLarkAgentXRetryFailed(request, response) {
	if (!larkAgentXIngressToken || request.headers['x-larkagentx-token'] !== larkAgentXIngressToken) {
		response.writeHead(401, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'unauthorized' }));
		return;
	}
	try {
		const input = await readJsonBody(request, 16 * 1024);
		const sourceKeys = Array.isArray(input?.source_keys) ? input.source_keys : [];
		const result = await groupRelay.retryFailedNow(sourceKeys);
		response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'ok', ...result }));
	} catch (error) {
		response.writeHead(400, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) }));
	}
}

async function renderMetrics() {
	const [rows, summary] = await Promise.all([ledger.metrics(), ledger.observability()]);
	const lines = ['# HELP ingestion_jobs Number of durable ingestion jobs by status and stage', '# TYPE ingestion_jobs gauge'];
	for (const row of rows) lines.push(`ingestion_jobs{status="${row.status}",stage="${row.stage}"} ${row.count}`);
	lines.push('# HELP ingestion_attempts_total Total recorded ingestion attempts', '# TYPE ingestion_attempts_total counter');
	for (const row of rows) lines.push(`ingestion_attempts_total{status="${row.status}",stage="${row.stage}"} ${row.attempts}`);
	let bytes = 0; let files = 0;
	for (const name of readdirSync(ingestionStorageDir)) { try { const stat = statSync(join(ingestionStorageDir, name)); if (stat.isFile()) { files++; bytes += stat.size; } } catch {} }
	lines.push('# HELP ingestion_temp_files Temporary media files on local disk', '# TYPE ingestion_temp_files gauge', `ingestion_temp_files ${files}`);
	lines.push('# HELP ingestion_temp_bytes Temporary media bytes on local disk', '# TYPE ingestion_temp_bytes gauge', `ingestion_temp_bytes ${bytes}`);
	lines.push('# HELP ingestion_queue_depth Durable jobs awaiting local or remote completion', '# TYPE ingestion_queue_depth gauge', `ingestion_queue_depth ${summary.queue_depth ?? 0}`);
	lines.push('# HELP ingestion_delivery_outbox_depth Durable n8n deliveries waiting for a worker lease', '# TYPE ingestion_delivery_outbox_depth gauge', `ingestion_delivery_outbox_depth ${summary.delivery_outbox_depth ?? 0}`);
	lines.push('# HELP ingestion_delivery_outbox_failed Terminal n8n deliveries requiring an operator retry', '# TYPE ingestion_delivery_outbox_failed gauge', `ingestion_delivery_outbox_failed ${summary.delivery_outbox_failed ?? 0}`);
	lines.push('# HELP ingestion_delivery_outbox_paused Deliveries explicitly paused by an operator and excluded from retry', '# TYPE ingestion_delivery_outbox_paused gauge', `ingestion_delivery_outbox_paused ${summary.delivery_outbox_paused ?? 0}`);
	lines.push('# HELP ingestion_operator_paused Explicitly paused ingestion jobs and excluded from failures', '# TYPE ingestion_operator_paused gauge', `ingestion_operator_paused ${summary.paused ?? 0}`);
	lines.push('# HELP ingestion_completed_media_bytes Total completed media bytes', '# TYPE ingestion_completed_media_bytes counter', `ingestion_completed_media_bytes ${summary.completed_media_bytes ?? 0}`);
	lines.push('# HELP ingestion_completed_seconds_mean Mean local completion duration in seconds', '# TYPE ingestion_completed_seconds_mean gauge', `ingestion_completed_seconds_mean ${summary.completed_seconds ?? 0}`);
	lines.push('# HELP ingestion_duplicate_ratio Completed or duplicate jobs that were duplicates', '# TYPE ingestion_duplicate_ratio gauge', `ingestion_duplicate_ratio ${(Number(summary.duplicates ?? 0) / Math.max(1, Number(summary.completed ?? 0) + Number(summary.duplicates ?? 0))).toFixed(6)}`);
	lines.push('# HELP ingestion_failed_jobs Durable failed jobs', '# TYPE ingestion_failed_jobs gauge', `ingestion_failed_jobs ${summary.failed ?? 0}`);
	return `${lines.join('\n')}\n`;
}

function asIsoString(value) {
	if (!value) return null;
	const timestamp = new Date(value).getTime();
	return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : null;
}

async function itouguDashboardStatus() {
	const unavailable = (message) => ({
		status: 'unavailable', state: 'unavailable', service: 'itougu-neican', observed_at: new Date().toISOString(),
		updated_at: null, heartbeat_age_seconds: null, message, products: [], product_targets: [],
		target_chat_ids: [], article_target_chat_ids: [], webhook_config: {},
	});
	if (!itouguStatusFile) return unavailable('未配置 Itougu 状态文件');
	try {
		const raw = JSON.parse(await readFile(itouguStatusFile, 'utf8'));
		const updatedAt = asIsoString(raw.updated_at);
		const heartbeatAgeSeconds = updatedAt ? Math.max(0, Math.floor((Date.now() - Date.parse(updatedAt)) / 1000)) : null;
		const staleAfterSeconds = Math.max(60, Number(raw.stale_after_seconds ?? 180));
		const rawState = String(raw.state ?? 'unknown');
		const state = heartbeatAgeSeconds === null ? 'unavailable'
			: heartbeatAgeSeconds > staleAfterSeconds ? 'delayed'
			: ['error', 'starting', 'polling', 'running', 'sleeping'].includes(rawState) ? rawState : 'unknown';
		const healthy = ['starting', 'polling', 'running', 'sleeping'].includes(state);
		const products = Array.isArray(raw.products) ? raw.products.map((item) => ({ business_product_id: String(item?.business_product_id ?? ''), name: String(item?.name ?? '') })).filter((item) => item.business_product_id && item.name) : [];
		const productTargets = Array.isArray(raw.product_targets) ? raw.product_targets.map((item) => ({
			business_product_id: String(item?.business_product_id ?? ''), name: String(item?.name ?? ''),
			target_chat_ids: Array.isArray(item?.target_chat_ids) ? item.target_chat_ids.map((value) => String(value)).filter(Boolean) : [],
			article_target_chat_ids: Array.isArray(item?.article_target_chat_ids) ? item.article_target_chat_ids.map((value) => String(value)).filter(Boolean) : [],
		})).filter((item) => item.business_product_id && item.name) : [];
		const keywordEntries = Array.isArray(raw.webhook_config?.keyword_entries) ? raw.webhook_config.keyword_entries.map((item) => ({ chat_id: String(item?.chat_id ?? ''), keyword: String(item?.keyword ?? '') })).filter((item) => item.chat_id && item.keyword) : [];
		const webhookConfig = {
			webhook_chat_ids: Array.isArray(raw.webhook_config?.webhook_chat_ids) ? raw.webhook_config.webhook_chat_ids.map((value) => String(value)).filter(Boolean) : [],
			keyword_chat_ids: Array.isArray(raw.webhook_config?.keyword_chat_ids) ? raw.webhook_config.keyword_chat_ids.map((value) => String(value)).filter(Boolean) : [],
			keyword_entries: keywordEntries,
			missing_keyword_chat_ids: Array.isArray(raw.webhook_config?.missing_keyword_chat_ids) ? raw.webhook_config.missing_keyword_chat_ids.map((value) => String(value)).filter(Boolean) : [],
			all_webhook_keywords_loaded: raw.webhook_config?.all_webhook_keywords_loaded === true,
		};
		return {
			status: healthy ? 'healthy' : state, state, service: 'itougu-neican', observed_at: new Date().toISOString(),
			updated_at: updatedAt, started_at: asIsoString(raw.started_at), heartbeat_age_seconds: heartbeatAgeSeconds,
			stale_after_seconds: staleAfterSeconds, message: raw.last_error ?? null,
			last_poll_started_at: asIsoString(raw.last_poll_started_at), last_poll_completed_at: asIsoString(raw.last_poll_completed_at),
			last_success_at: asIsoString(raw.last_success_at), last_error: raw.last_error ? String(raw.last_error).slice(0, 500) : null,
			last_trigger: raw.last_trigger ? String(raw.last_trigger) : null, manual_refresh_count: Number(raw.manual_refresh_count ?? 0),
			consecutive_error_count: Number(raw.consecutive_error_count ?? 0), poll_count: Number(raw.poll_count ?? 0),
			failure_count: Number(raw.failure_count ?? 0), sent_count: Number(raw.sent_count ?? 0),
			interval_seconds: Number(raw.interval_seconds ?? 0), off_hours_interval_seconds: Number(raw.off_hours_interval_seconds ?? 0),
			midday_interval_seconds: Number(raw.midday_interval_seconds ?? 0), trading_hours_only: raw.trading_hours_only === true,
			current_window: raw.current_window ? String(raw.current_window) : null, next_poll_in_seconds: Number(raw.next_poll_in_seconds ?? 0),
			products, product_targets: productTargets,
			target_chat_ids: Array.isArray(raw.target_chat_ids) ? raw.target_chat_ids.map((value) => String(value)).filter(Boolean) : [],
			article_target_chat_ids: Array.isArray(raw.article_target_chat_ids) ? raw.article_target_chat_ids.map((value) => String(value)).filter(Boolean) : [],
			webhook_config: webhookConfig,
		};
	} catch (error) {
		return unavailable(error?.code === 'ENOENT' ? 'Itougu 状态文件尚未生成' : `Itougu 状态不可读：${error?.message ?? error}`);
	}
}

let itouguRefreshInFlight = null;
async function triggerItouguRefresh() {
	if (itouguRefreshInFlight) return itouguRefreshInFlight;
	itouguRefreshInFlight = (async () => {
		let upstream;
		try {
			upstream = await fetch(`${itouguControlUrl}/refresh`, {
				method: 'POST', headers: { accept: 'application/json' }, signal: AbortSignal.timeout(120_000),
			});
		} catch (error) {
			throw new Error(`Itougu 手动刷新接口不可用：${error instanceof Error ? error.message : String(error)}`);
		}
		const body = await upstream.text();
		let payload;
		try { payload = body ? JSON.parse(body) : {}; } catch { throw new Error('Itougu 手动刷新接口返回了非 JSON 响应'); }
		return { http_status: upstream.status, payload };
	})().finally(() => { itouguRefreshInFlight = null; });
	return itouguRefreshInFlight;
}

async function groupRelayDashboardStatus() {
	const [persistedSources, routes, oauth, ingestionSources, writer, delivery, larkagentx, itougu] = await Promise.all([ledger.relayStatus(), ledger.relayRoutes(), feishuUserOauth.status(), ledger.ingestionStatusBySource(), ledger.relayWriterStatus(), ledger.observability(), larkAgentXDashboardStatus(), itouguDashboardStatus()]);
	const runtime = groupRelay.status();
	const listenerRuntime = summaryListener.status();
	const chatStateById = new Map((await ledger.groupChatStates().catch(() => [])).map((state) => [String(state.chat_id), state]));
	const persistedByKey = new Map(persistedSources.map((source) => [source.source_key, source]));
	const runtimeByKey = new Map(runtime.sources.map((source) => [source.key, source]));
	const ingestionByTag = new Map(ingestionSources.map((source) => [source.source_tag, source]));
	const websocketChatIdsBySource = new Map();
	const addWebsocketRoute = (chatId, sourceKey) => {
		// The route map keeps official oc_ aliases for documentation and
		// deduplication, but only numeric ids are actual LarkAgentX socket ids.
		if (!/^\d+$/.test(chatId) || !String(sourceKey ?? '').trim()) return;
		if (!websocketChatIdsBySource.has(sourceKey)) websocketChatIdsBySource.set(sourceKey, []);
		if (!websocketChatIdsBySource.get(sourceKey).includes(chatId)) websocketChatIdsBySource.get(sourceKey).push(chatId);
	};
	for (const [chatId, sourceKey] of [...larkAgentXGroupRelayRoutes, ...larkAgentXDynamicGroupRelayRoutes]) {
		addWebsocketRoute(chatId, sourceKey);
	}
	// A bridge restart restores dynamic numeric ids before the first new event.
	// Merge that durable view into the dashboard projection so a quiet source is
	// shown as WebSocket-bound immediately instead of waiting for another event.
	for (const [chatId, route] of Object.entries(larkagentx.dynamic_routes ?? {})) {
		addWebsocketRoute(chatId, String(route?.source_key ?? '').trim());
	}
	for (const [sourceKey, binding] of Object.entries(larkagentx.persisted_route_bindings ?? {})) {
		addWebsocketRoute(String(binding?.chat_id ?? '').trim(), String(binding?.source_key ?? sourceKey).trim());
	}
	const staleAfterSeconds = Math.max(45, groupRelayConfig.intervalSeconds * 3);
	const now = Date.now();
	const sources = routes.map((source) => {
		const persisted = persistedByKey.get(source.key);
		const current = runtimeByKey.get(source.key);
		const lastPolledAt = asIsoString(persisted?.last_polled_at);
		const pollAgeSeconds = lastPolledAt ? Math.max(0, Math.floor((now - Date.parse(lastPolledAt)) / 1000)) : null;
		const failedCount = Number(persisted?.failed_count ?? 0);
		const websocketChatIds = websocketChatIdsBySource.get(source.key) ?? [];
		const websocketStats = websocketChatIds.map((chatId) => larkagentx.chat_stats?.[chatId]).filter(Boolean);
		const websocketObservedCount = websocketStats.reduce((sum, value) => sum + Number(value.observed_count ?? 0), 0);
		const websocketForwardedCount = websocketStats.reduce((sum, value) => sum + Number(value.forwarded_count ?? 0), 0);
		const websocketFailedCount = websocketStats.reduce((sum, value) => sum + Number(value.failed_count ?? 0), 0);
		const websocketLastObservedAt = websocketStats.map((value) => asIsoString(value.last_observed_at)).filter(Boolean).sort().at(-1) ?? null;
		const websocketLastForwardedAt = websocketStats.map((value) => asIsoString(value.last_forwarded_at)).filter(Boolean).sort().at(-1) ?? null;
		const ingestionRecord = ingestionByTag.get(source.tag) ?? null;
		const ingestionLastUpdatedAt = asIsoString(ingestionRecord?.last_updated_at);
		const ingestionAgeSeconds = ingestionLastUpdatedAt ? Math.max(0, Math.floor((now - Date.parse(ingestionLastUpdatedAt)) / 1000)) : null;
		const ingestionState = !ingestionRecord ? 'awaiting_message'
			: isOperatorPausedIngestion(ingestionRecord) ? 'paused'
			: ingestionRecord.latest_status === 'filtered' ? 'filtered'
			: ingestionRecord.latest_status === 'completed' ? 'completed'
			: ['failed', 'retryable_failed'].includes(ingestionRecord.latest_status) ? 'failed'
			: ingestionAgeSeconds !== null && ingestionAgeSeconds > Math.max(120, summaryListenerConfig.intervalSeconds * 12) ? 'stalled'
			: 'processing';
		const ingestionFailed = ['failed', 'stalled'].includes(ingestionState);
		const lastForwardedAt = asIsoString(persisted?.last_forwarded_at);
		const effectiveLastForwardedAt = websocketChatIds.length ? websocketLastForwardedAt ?? lastForwardedAt : lastForwardedAt;
		const deliveryState = websocketChatIds.length
			? websocketFailedCount > 0 ? 'failed' : websocketForwardedCount > 0 ? 'verified' : 'awaiting_message'
			: failedCount > 0 ? 'failed' : lastForwardedAt ? 'verified' : 'awaiting_message';
		const activeError = current?.last_error
			?? (ingestionFailed ? ingestionRecord?.error_message ?? null : null);
		const resolvedError = !activeError && failedCount === 0
			? persisted?.latest_failure_error ?? ingestionRecord?.latest_failure_error ?? null : null;
		const resolvedErrorAt = persisted?.latest_failure_at ?? ingestionRecord?.latest_failure_at ?? null;
		const transport = websocketChatIds.length ? 'larkagentx_websocket' : groupRelayConfig.enabled ? 'oauth_poll' : larkAgentXDynamicRouteDiscoveryEnabled ? 'pending_websocket_discovery' : 'disabled';
		// What the membership check and the history reader know about this route's group.
		// An archived route has no binding any more; its groups are the ones it had when it was archived.
		const chatStates = (source.archivedAt ? source.archivedChatIds : websocketChatIds).map((chatId) => chatStateById.get(chatId)).filter(Boolean);
		const goneState = source.archivedAt ? { membership: source.archivedReason ?? 'left', left_at: chatStates[0]?.left_at ?? source.archivedAt }
			: chatStates.length && chatStates.every((item) => item.membership !== 'member') ? chatStates[0] : null;
		const historyState = chatStates.find((item) => item.membership === 'member') ?? chatStates[0] ?? null;
		let state = 'healthy';
		if (source.archivedAt) state = goneState.membership;
		else if (source.enabled === false || transport === 'disabled') state = 'disabled';
		else if (goneState) state = goneState.membership;
		else if (transport === 'pending_websocket_discovery') state = 'starting';
		else if (transport === 'larkagentx_websocket') {
			if (larkagentx.status !== 'healthy' || larkagentx.websocket?.state !== 'connected') state = 'unavailable';
			else if (websocketChatIds.some((chatId) => larkagentx.chat_validation?.[chatId]?.state === 'error')) state = 'degraded';
			else if (websocketFailedCount > 0 || ingestionFailed) state = 'degraded';
		} else if (!oauth.configured) state = 'not_configured';
		else if (oauth.scope_audit?.verified === false) state = 'not_authorized';
		else if (current?.state === 'error' || current?.state === 'unavailable') state = current.state;
		else if (!persisted || pollAgeSeconds === null) state = 'starting';
		else if (pollAgeSeconds > staleAfterSeconds) state = 'delayed';
		else if (failedCount > 0 || ingestionFailed) state = 'degraded';
		return {
			key: source.key, tag: source.tag, chat_name: source.chatName, source_chat_id: source.chatId ?? null, target_chat_ids: source.targetChatIds ?? [], enabled: source.enabled !== false,
			membership: goneState ? goneState.membership : chatStates.length ? 'member' : null, left_at: asIsoString(goneState?.left_at),
			archived: Boolean(source.archivedAt), archived_at: asIsoString(source.archivedAt),
			membership_checked_at: asIsoString(historyState?.checked_at), history: historyState ? historyProgress(historyState) : null,
			transport, websocket_chat_ids: websocketChatIds, websocket_observed_count: websocketObservedCount, websocket_forwarded_count: websocketForwardedCount,
			websocket_failed_count: websocketFailedCount, websocket_last_observed_at: websocketLastObservedAt, websocket_last_forwarded_at: websocketLastForwardedAt,
			state, last_polled_at: lastPolledAt, poll_age_seconds: pollAgeSeconds,
			last_source_message_at: asIsoString(persisted?.last_source_message_at),
			last_forwarded_at: effectiveLastForwardedAt,
			// Polling proves source readability. A real delivery is separately
			// visible so a quiet source group is never mistaken for a send test.
			delivery_state: deliveryState,
			last_reconciled_at: current?.last_reconciled_at ?? null,
			last_message_status: persisted?.last_message_status ?? null,
			failed_count: failedCount,
			ingestion: ingestionRecord ? {
				job_count: Number(ingestionRecord.job_count ?? 0), completed_count: Number(ingestionRecord.completed_count ?? 0), failed_count: Number(ingestionRecord.failed_count ?? 0), paused_count: Number(ingestionRecord.paused_count ?? 0),
				state: ingestionState, latest_status: ingestionRecord.latest_status, latest_stage: ingestionRecord.latest_stage, remote_batch_id: ingestionRecord.remote_batch_id ?? null,
				last_updated_at: ingestionLastUpdatedAt, error_class: ingestionRecord.error_class ?? null, error_message: ingestionRecord.error_message ?? null,
				latest_failure_error: ingestionRecord.latest_failure_error ?? null, latest_failure_at: asIsoString(ingestionRecord.latest_failure_at),
			} : null,
			last_error: activeError,
			last_resolved_error: resolvedError,
			last_resolved_error_at: resolvedError ? asIsoString(resolvedErrorAt) : null,
		};
	});
	// A group the account has left is listed apart from the live ones, with what was kept of it.
	const savedByKey = await savedContentForRoutes(routes, sources.filter((source) => source.archived || ['left', 'dissolved'].includes(source.membership)).map((source) => source.key))
		.catch((error) => { console.warn(`已退群的保存内容统计失败：${error instanceof Error ? error.message : String(error)}`); return new Map(); });
	for (const source of sources) source.saved = savedByKey.get(source.key) ?? null;
	const listenerLastSuccessAt = asIsoString(listenerRuntime.last_success_at);
	const listenerPollAgeSeconds = listenerLastSuccessAt ? Math.max(0, Math.floor((now - Date.parse(listenerLastSuccessAt)) / 1000)) : null;
	const listenerState = !listenerRuntime.enabled ? 'disabled'
		: !oauth.configured ? 'not_configured'
		: oauth.scope_audit?.verified === false ? 'not_authorized'
		: listenerRuntime.state === 'error' ? 'error'
		: listenerPollAgeSeconds === null ? 'starting'
		: listenerPollAgeSeconds > Math.max(45, summaryListenerConfig.intervalSeconds * 3) ? 'delayed'
		: 'healthy';
	const activeSources = sources.filter((source) => source.enabled && source.transport !== 'disabled');
	const overall = activeSources.length > 0 && activeSources.every((source) => source.state === 'healthy') ? 'healthy'
		: sources.some((source) => ['error', 'unavailable', 'delayed', 'degraded', 'not_configured', 'not_authorized'].includes(source.state) || (source.enabled && ['left', 'dissolved'].includes(source.state))) ? 'degraded'
		: 'starting';
	const realtimeSummaryHealthy = larkagentx.status === 'healthy' && larkagentx.websocket?.state === 'connected' && larkagentx.summary_ingress_configured;
	const itouguHealthy = itougu.status === 'healthy';
	const combinedOverall = overall === 'healthy' && realtimeSummaryHealthy && itouguHealthy ? 'healthy'
		: overall === 'degraded' || ['error', 'delayed', 'not_configured', 'not_authorized'].includes(listenerState) && listenerState !== 'disabled' ? 'degraded'
		: !itouguHealthy ? 'degraded' : overall;
	return {
		status: combinedOverall, observed_at: new Date(now).toISOString(), enabled: groupRelayConfig.enabled, realtime_enabled: larkAgentXGroupRelayEnabled,
		interval_seconds: groupRelayConfig.intervalSeconds, stale_after_seconds: staleAfterSeconds,
		user_oauth_configured: Boolean(oauth.configured), target_configured: Boolean(groupRelayConfig.targetChatId),
		user_oauth_scope_audit: oauth.scope_audit ?? null,
		user_oauth_refresh_expires_at: oauth.refresh_expires_at ?? null, user_oauth_access_expires_at: oauth.access_expires_at ?? null,
		webhook_config: webhookConfigStatus(groupRelayWebhooksByChatId, groupRelayWebhookKeywordsByChatId),
		webhook_targets: webhookTargets.list(),
		source_tags: sourceTags.list(),
		larkagentx,
		itougu,
		delivery_verified: sources.filter((source) => source.enabled).every((source) => source.delivery_state === 'verified'),
		last_tick_started_at: runtime.last_tick_started_at, last_tick_completed_at: runtime.last_tick_completed_at,
		last_tick_error: runtime.last_tick_error,
		writer: {
			configured_id: relayWriterId || null,
			state: runtime.writer_state,
			owner_id: writer?.writer_id ?? null,
			generation: writer?.generation ?? null,
			updated_at: asIsoString(writer?.updated_at),
		},
		delivery_outbox: {
			depth: Number(delivery?.delivery_outbox_depth ?? 0),
			failed: Number(delivery?.delivery_outbox_failed ?? 0),
			paused: Number(delivery?.delivery_outbox_paused ?? 0),
		},
		sources,
		summary_listener: {
			...listenerRuntime, state: listenerState, last_success_at: listenerLastSuccessAt,
			poll_age_seconds: listenerPollAgeSeconds,
		},
	};
}

async function larkAgentXDashboardStatus() {
	const unavailable = (message) => ({ status: 'unavailable', observed_at: new Date().toISOString(), message, metrics_persisted: false, metrics_source: null, websocket: { state: 'unavailable' }, listen_chat_count: 0, listen_chat_ids: [], websocket_chat_ids: [], summary_chat_ids: [], summary_ingress_configured: false, gap_repair_enabled: false, private_gap_repair_enabled: false, private_gap_repair_on_start: false, private_repair_count: 0, private_repair_message_count: 0, private_repair_failed_count: 0, dynamic_route_discovery: false, route_catalog_count: 0, route_catalog_last_refresh_at: null, route_catalog_error: null, dynamic_routes: {}, persisted_route_bindings: {}, chat_validation: {}, chat_stats: {}, observed_count: 0, forwarded_count: 0, filtered_count: 0, ignored_count: 0, ignored_by_chat: {}, position_stats: {}, position_summary: { missing_position_count: 0, gap_event_count: 0, out_of_order_count: 0, recovered_position_count: 0, unresolved_position_count: 0 } });
	if (!larkAgentXHealthUrl) return unavailable('未配置 LarkAgentX health 地址');
	try {
		const controller = new AbortController();
		const timer = setTimeout(() => controller.abort(), 1500);
		let response;
		try { response = await fetch(larkAgentXHealthUrl, { signal: controller.signal }); } finally { clearTimeout(timer); }
		if (!response.ok) return unavailable(`LarkAgentX health HTTP ${response.status}`);
		const raw = await response.json();
		const validation = Object.fromEntries(Object.entries(raw.chat_validation ?? {}).map(([chatId, value]) => [chatId, {
			state: value?.state ?? 'unknown', name: value?.name ?? null, checked_at: value?.checked_at ?? null, error: value?.error ?? null,
		}]));
		const stats = Object.fromEntries(Object.entries(raw.chat_stats ?? {}).map(([chatId, value]) => [chatId, {
			allowlisted: value?.allowlisted !== false, observed_count: Number(value?.observed_count ?? 0), self_message_count: Number(value?.self_message_count ?? 0),
				forwarded_count: Number(value?.forwarded_count ?? 0), failed_count: Number(value?.failed_count ?? 0), filtered_count: Number(value?.filtered_count ?? 0), historical_failed_count: Number(value?.historical_failed_count ?? value?.failed_count ?? 0), last_observed_at: value?.last_observed_at ?? null,
			last_forwarded_at: value?.last_forwarded_at ?? null, last_message_type: value?.last_message_type ?? null,
		}]));
		return {
			status: raw.status === 'ok' ? 'healthy' : 'degraded', observed_at: new Date().toISOString(), release: raw.release ?? null,
			metrics_persisted: raw.metrics_persisted === true, metrics_source: raw.metrics_source ?? null,
			websocket: raw.websocket ?? { state: 'unknown' }, listen_chat_count: Number(raw.listen_chat_count ?? 0),
			listen_chat_ids: Array.isArray(raw.listen_chat_ids) ? raw.listen_chat_ids : [], websocket_chat_ids: Array.isArray(raw.websocket_chat_ids) ? raw.websocket_chat_ids : [],
				summary_chat_ids: Array.isArray(raw.summary_chat_ids) ? raw.summary_chat_ids : [], summary_ingress_configured: Boolean(raw.summary_ingress_configured),
				gap_repair_enabled: Boolean(raw.gap_repair_enabled), private_gap_repair_enabled: Boolean(raw.private_gap_repair_enabled),
				private_gap_repair_on_start: Boolean(raw.private_gap_repair_on_start), private_repair_count: Number(raw.private_repair_count ?? 0),
				private_repair_message_count: Number(raw.private_repair_message_count ?? 0), private_repair_failed_count: Number(raw.private_repair_failed_count ?? 0),
				dynamic_route_discovery: Boolean(raw.dynamic_route_discovery),
				route_catalog_count: Number(raw.route_catalog_count ?? 0), route_catalog_last_refresh_at: raw.route_catalog_last_refresh_at ?? null,
				route_catalog_error: raw.route_catalog_error ?? null, dynamic_routes: raw.dynamic_routes && typeof raw.dynamic_routes === 'object' ? raw.dynamic_routes : {},
				persisted_route_bindings: raw.persisted_route_bindings && typeof raw.persisted_route_bindings === 'object' ? raw.persisted_route_bindings : {},
				mapping_check_at: raw.mapping_check_at ?? null, mapping_check_error: raw.mapping_check_error ?? null,
				observed_count: Number(raw.observed_count ?? 0), forwarded_count: Number(raw.forwarded_count ?? 0), failed_count: Number(raw.failed_count ?? 0), filtered_count: Number(raw.filtered_count ?? 0), ignored_count: Number(raw.ignored_count ?? 0),
				ignored_by_chat: raw.ignored_by_chat && typeof raw.ignored_by_chat === 'object' ? raw.ignored_by_chat : {},
				position_stats: raw.position_stats && typeof raw.position_stats === 'object' ? raw.position_stats : {},
				position_summary: raw.position_summary && typeof raw.position_summary === 'object' ? raw.position_summary : { missing_position_count: 0, gap_event_count: 0, out_of_order_count: 0, recovered_position_count: 0, unresolved_position_count: 0 },
			auth: raw.auth && typeof raw.auth === 'object' ? raw.auth : null,
			ws_notify_cmds: Array.isArray(raw.ws_notify_cmds) ? raw.ws_notify_cmds : [],
			ws_notify_count: Number(raw.ws_notify_count ?? 0), last_ws_notify: raw.last_ws_notify ?? null,
			private_tail_repair_chat_ids: Array.isArray(raw.private_tail_repair_chat_ids) ? raw.private_tail_repair_chat_ids : [],
			decode_error_count: Number(raw.decode_error_count ?? 0), decode_fallback_count: Number(raw.decode_fallback_count ?? 0), unknown_field_count: Number(raw.unknown_field_count ?? 0),
			partial_frame_count: Number(raw.partial_frame_count ?? 0), retry_count: Number(raw.retry_count ?? 0), last_observed_chat_id: raw.last_observed_chat_id ?? null,
			last_observed_message_type: raw.last_observed_message_type ?? null, event_spool: {
				queued: Number(raw.event_spool?.queued ?? 0), processing: Number(raw.event_spool?.processing ?? 0), pending: Number(raw.event_spool?.pending ?? 0),
				failed: Number(raw.event_spool?.failed ?? 0), delivered: Number(raw.event_spool?.delivered ?? 0),
			}, chat_validation: validation, chat_stats: stats,
		};
	} catch (error) {
		return unavailable(error?.name === 'AbortError' ? 'LarkAgentX health 超时' : `LarkAgentX health 不可用：${error?.message ?? error}`);
	}
}

async function handleLarkAgentXHistoryExport(request, response, url) {
	const chatId = String(url.searchParams.get('chat_id') ?? '').trim();
	if (!/^\d{1,64}$/.test(chatId)) {
		response.writeHead(400, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'error', message: '必须选择有效的 WebSocket 数字 chat_id' }));
		return;
	}
	// Incremental export: `after_sequence` resumes from a caller-kept cursor,
	// so a tool exports only what is new since last time. `days` stays as a
	// convenience window; an explicit after_sequence takes precedence over it.
	// `mode=incremental` lets the browser use a durable server-side bookmark
	// instead of carrying its own cursor: the bookmark supplies after_sequence
	// and is advanced from the response headers after a successful export.
	const mode = String(url.searchParams.get('mode') ?? '').trim().toLowerCase();
	const useBookmark = mode === 'incremental';
	let afterSequenceRaw = String(url.searchParams.get('after_sequence') ?? '').trim();
	if (useBookmark) {
		const bookmark = await ledger.getExportBookmark(chatId).catch(() => null);
		afterSequenceRaw = String(bookmark?.next_sequence ?? 0);
	}
	const afterSequence = afterSequenceRaw === '' ? null : Number(afterSequenceRaw);
	if (afterSequence !== null && (!Number.isInteger(afterSequence) || afterSequence < 0)) {
		response.writeHead(400, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'error', message: 'after_sequence 必须是非负整数' }));
		return;
	}
	const format = String(url.searchParams.get('format') ?? 'ndjson').trim().toLowerCase();
	if (!['ndjson', 'transcript'].includes(format)) {
		response.writeHead(400, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'error', message: 'format 只支持 ndjson 或 transcript' }));
		return;
	}
	const days = Number(url.searchParams.get('days') ?? '1');
	if (afterSequence === null && ![1, 7, 30].includes(days)) {
		response.writeHead(400, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'error', message: '导出范围只支持最近 1 天、7 天或 30 天（或用 after_sequence 增量）' }));
		return;
	}
	const exportSource = String(url.searchParams.get('source') ?? 'archive').trim().toLowerCase();
	if (!['archive', 'ledger'].includes(exportSource)) {
		response.writeHead(400, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'error', message: 'source 只支持 archive 或 ledger' }));
		return;
	}
	if (exportSource === 'ledger') {
		// Degraded groups archive a placeholder; the ledger holds the OAuth-
		// backfilled official content. Window export only: the ledger has no
		// bridge sequence, so an incremental cursor cannot apply to it.
		if (format !== 'transcript' || afterSequence !== null || useBookmark) {
			response.writeHead(400, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({ status: 'error', message: 'source=ledger 仅支持 format=transcript 的固定天数窗口' }));
			return;
		}
		try {
			const toMs = Date.now();
			const rows = await ledger.relayMessagesByChatWindow(chatId, toMs - days * 86_400_000, toMs);
			const body = Buffer.from(renderLedgerTranscript(rows), 'utf8');
			const times = rows.map((row) => Number(row.source_create_time)).filter(Boolean);
			response.writeHead(200, {
				'content-type': 'text/plain; charset=utf-8',
				'content-disposition': `attachment; filename="larkagentx-${chatId}-ledger-last-${days}d.txt"`,
				'cache-control': 'no-store',
				'x-larkagentx-export-source': 'ledger',
				'x-larkagentx-event-count': String(rows.length),
				...(times.length ? { 'x-larkagentx-from-time': String(Math.min(...times) / 1000), 'x-larkagentx-to-time': String(Math.max(...times) / 1000) } : {}),
			});
			response.end(body);
		} catch (error) {
			response.writeHead(503, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({ status: 'error', message: `账本导出失败：${error instanceof Error ? error.message : String(error)}` }));
		}
		return;
	}
	if (!larkAgentXIngressToken || !larkAgentXHealthUrl) {
		response.writeHead(503, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'error', message: 'LarkAgentX 导出服务未配置' }));
		return;
	}
	try {
		const bridgeUrl = new URL(larkAgentXHealthUrl);
		bridgeUrl.pathname = format === 'transcript' ? '/history/transcript' : '/history/export';
		bridgeUrl.search = '';
		bridgeUrl.searchParams.set('chat_id', chatId);
		if (afterSequence !== null) {
			bridgeUrl.searchParams.set('after_sequence', String(afterSequence));
		} else {
			bridgeUrl.searchParams.set('from_time', String((Date.now() - days * 24 * 60 * 60 * 1000) / 1000));
			bridgeUrl.searchParams.set('to_time', String(Date.now() / 1000));
		}
		bridgeUrl.searchParams.set('limit', '100000');
		const accept = format === 'transcript' ? 'text/plain' : 'application/x-ndjson';
		const upstream = await fetch(bridgeUrl, { headers: { 'x-larkagentx-token': larkAgentXIngressToken, accept } });
		if (!upstream.ok) {
			const message = (await upstream.text()).slice(0, 500);
			response.writeHead(upstream.status === 401 ? 502 : upstream.status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({ status: 'error', message: `LarkAgentX 历史导出失败：${message || upstream.statusText}` }));
			return;
		}
		if (useBookmark) {
			// Headers are available before the body streams; advance the durable
			// cursor now so the next incremental export resumes correctly even if
			// the client aborts the download midway.
			await ledger.advanceExportBookmark(chatId, {
				nextSequence: Number(upstream.headers.get('x-larkagentx-next-sequence') ?? afterSequence ?? 0),
				fromTime: upstream.headers.get('x-larkagentx-from-time') ?? null,
				toTime: upstream.headers.get('x-larkagentx-to-time') ?? null,
				eventCount: Number(upstream.headers.get('x-larkagentx-event-count') ?? 0),
				format,
			}).catch((error) => console.error(`导出书签推进失败 chat=${chatId}: ${error instanceof Error ? error.message : String(error)}`));
		}
		const safeFileChatId = chatId.replace(/[^0-9]/g, '');
		const ext = format === 'transcript' ? 'txt' : 'jsonl';
		const windowTag = useBookmark ? 'incremental' : (afterSequence !== null ? `after-${afterSequence}` : `last-${days}d`);
		const passHeader = (name) => (upstream.headers.get(name) ? { [name]: upstream.headers.get(name) } : {});
		response.writeHead(200, {
			'content-type': format === 'transcript' ? 'text/plain; charset=utf-8' : 'application/x-ndjson; charset=utf-8',
			'content-disposition': `attachment; filename="larkagentx-${safeFileChatId}-${windowTag}.${ext}"`,
			'cache-control': 'no-store',
			...passHeader('x-larkagentx-event-count'),
			...passHeader('x-larkagentx-next-sequence'),
			...passHeader('x-larkagentx-from-time'),
			...passHeader('x-larkagentx-to-time'),
		});
		if (!upstream.body) {
			response.end();
			return;
		}
		Readable.fromWeb(upstream.body).pipe(response);
	} catch (error) {
		if (!response.headersSent) response.writeHead(502, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		if (!response.writableEnded) response.end(JSON.stringify({ status: 'error', message: `LarkAgentX 历史导出不可用：${error instanceof Error ? error.message : String(error)}` }));
	}
}

const MAX_BUNDLE_MESSAGES = 20_000;
const MAX_BUNDLE_IMAGE_BYTES = 20 * 1024 * 1024;

// Numeric WebSocket ids per route, from every place one can be recorded: the
// static env map, what inbound messages taught this process, and the bridge's
// persisted bindings.
async function numericChatIdsByRoute() {
	const byKey = new Map();
	const add = (chatId, sourceKey) => {
		const id = String(chatId ?? '').trim();
		const key = String(sourceKey ?? '').trim();
		if (!/^\d+$/.test(id) || !key) return;
		if (!byKey.has(key)) byKey.set(key, []);
		if (!byKey.get(key).includes(id)) byKey.get(key).push(id);
	};
	for (const chatId of new Set([...larkAgentXGroupRelayRoutes.keys(), ...larkAgentXDynamicGroupRelayRoutes.keys()])) add(chatId, larkAgentXSourceKeyForChat(chatId));
	const bridge = await larkAgentXDashboardStatus().catch(() => null);
	for (const [chatId, route] of Object.entries(bridge?.dynamic_routes ?? {})) add(chatId, route?.source_key);
	for (const [sourceKey, binding] of Object.entries(bridge?.persisted_route_bindings ?? {})) add(binding?.chat_id, binding?.source_key ?? sourceKey);
	return byKey;
}

async function readBounded(stream, limit) {
	const chunks = [];
	let size = 0;
	for await (const chunk of stream) {
		size += chunk.length;
		if (size > limit) throw new Error(`图片超过 ${Math.floor(limit / 1024 / 1024)} MiB`);
		chunks.push(Buffer.from(chunk));
	}
	return Buffer.concat(chunks);
}

// A WebSocket-era image is downloaded through the bridge and decrypted with
// the key the ledger kept; an older, officially read one comes from the
// message API with the user's token.
async function fetchBundleImage(ref, row) {
	if (ref.larkagentx) {
		if (!larkAgentXResourceUrl) throw new Error('LarkAgentX 图片下载未配置');
		const url = new URL(larkAgentXResourceUrl);
		for (const [key, value] of Object.entries(ref.larkagentx)) {
			if (value !== null && value !== undefined && String(value).trim()) url.searchParams.set(key, String(value));
		}
		const fetched = await fetch(url, { headers: { 'x-larkagentx-token': larkAgentXIngressToken }, signal: AbortSignal.timeout(60_000) });
		if (!fetched.ok || !fetched.body) throw new Error(`LarkAgentX 图片资源 HTTP ${fetched.status}`);
		return decryptLarkAgentXImage(await readBounded(Readable.fromWeb(fetched.body), MAX_BUNDLE_IMAGE_BYTES + 16), ref.larkagentx);
	}
	const messageId = String(row?.source_message_id ?? '');
	if (!messageId.startsWith('om_')) throw new Error('该图片没有可用的下载凭据');
	const resource = await feishuUserOauth.sourceApi.messageResourceGet({ messageId, fileKey: ref.key, type: 'image' });
	return readBounded(resource.getReadableStream(), MAX_BUNDLE_IMAGE_BYTES);
}

async function bridgePostJson(pathname, payload, timeoutMs) {
	if (!larkAgentXIngressToken || !larkAgentXHealthUrl) throw new Error('LarkAgentX 桥接未配置');
	const bridgeUrl = new URL(larkAgentXHealthUrl);
	bridgeUrl.pathname = pathname;
	bridgeUrl.search = '';
	const upstream = await fetch(bridgeUrl, {
		method: 'POST', headers: { 'x-larkagentx-token': larkAgentXIngressToken, 'content-type': 'application/json' },
		body: JSON.stringify(payload), signal: AbortSignal.timeout(timeoutMs),
	});
	const body = await upstream.json().catch(() => ({}));
	if (!upstream.ok) throw new Error(String(body?.message ?? `bridge HTTP ${upstream.status}`).slice(0, 200));
	return body;
}

// What only the bridge knows: the picture keys of older cards (kept in its
// event spool) and the display name behind a numeric sender id.  Both are
// extras: when the bridge cannot answer, the export goes out without them.
async function bundleContext(rows, { names = true } = {}) {
	const cardImages = new Map();
	const senderNames = new Map();
	const wanted = rows.filter(cardNeedsImageLookup).map((row) => ({ chat_id: String(row.source_chat_id), message_id: String(row.source_message_id) }));
	try {
		// The bridge accepts a 64 KiB body; 400 id pairs stay well inside it.
		for (let start = 0; start < wanted.length; start += 400) {
			const result = await bridgePostJson('/history/card-images', { messages: wanted.slice(start, start + 400) }, 30_000);
			for (const [messageId, images] of Object.entries(result?.images ?? {})) cardImages.set(messageId, images);
		}
	} catch (error) {
		console.warn(`资料包：卡片图片查询失败，卡片只导出文字：${error instanceof Error ? error.message : String(error)}`);
	}
	if (names) {
		try {
			for (const [chatId, userIds] of unnamedSendersByChat(rows)) {
				for (let start = 0; start < userIds.length; start += 200) {
					const result = await bridgePostJson('/users/names', { chat_id: chatId, user_ids: userIds.slice(start, start + 200) }, 120_000);
					for (const [userId, name] of Object.entries(result?.names ?? {})) senderNames.set(userId, name);
				}
			}
		} catch (error) {
			console.warn(`资料包：发送者名字解析失败，以 ID 显示：${error instanceof Error ? error.message : String(error)}`);
		}
	}
	return { cardImages, senderNames };
}

// The numeric group ids of every route, as an export needs them.  An archived
// route keeps the ids it had, so its group's saved content is still found.
async function exportNumericIdsByRoute(routes) {
	const numericIdsByKey = await numericChatIdsByRoute();
	for (const item of routes) if (item.archivedAt && item.archivedChatIds.length) numericIdsByKey.set(item.key, [...new Set([...(numericIdsByKey.get(item.key) ?? []), ...item.archivedChatIds])]);
	return numericIdsByKey;
}

async function routeExportChatIds(route, routes, numericIdsByKey) {
	return resolveRouteChatIds({ route, routes, numericIdsByKey, seenUnderKey: await ledger.relayChatIdsBySourceKey(route.key) });
}

// What an export of each left group can still contain.  Nothing is added to a
// group the account is no longer in, so the answer is kept for a few minutes
// instead of being counted on every dashboard refresh.
const SAVED_CONTENT_TTL_MS = 5 * 60_000;
let savedContentCache = { key: '', at: 0, value: new Map() };
async function savedContentForRoutes(routes, sourceKeys) {
	const keys = [...new Set(sourceKeys)].sort();
	if (!keys.length) return new Map();
	const cacheKey = keys.join(',');
	if (savedContentCache.key === cacheKey && Date.now() - savedContentCache.at < SAVED_CONTENT_TTL_MS) return savedContentCache.value;
	const numericIdsByKey = await exportNumericIdsByRoute(routes);
	const chatIdsByKey = new Map();
	for (const key of keys) {
		const route = routes.find((item) => item.key === key);
		if (route) chatIdsByKey.set(key, await routeExportChatIds(route, routes, numericIdsByKey));
	}
	const byChat = await ledger.savedContentByChat([...chatIdsByKey.values()].flat());
	const value = new Map([...chatIdsByKey].map(([key, chatIds]) => [key, { ...savedContentSummary(chatIds, byChat), chat_ids: chatIds }]));
	savedContentCache = { key: cacheKey, at: Date.now(), value };
	return value;
}

// The group's own history, read through the bridge by position.  This is what
// makes an export complete: it includes what the relay never forwarded and
// what was said before the relay listened to the group.
async function locateGroupHistory(chatIds, range) {
	const located = [];
	for (const chatId of chatIds.filter((value) => /^\d+$/.test(value))) {
		const result = await bridgePostJson('/history/locate', { chat_id: chatId, from_time: range.fromMs / 1000, to_time: range.toMs / 1000 }, 120_000);
		if (Number(result?.count) > 0) located.push({ chatId, start: Number(result.start), end: Number(result.end), count: Number(result.count) });
	}
	return located;
}

async function readGroupHistory(located, range) {
	const rows = [];
	for (const { chatId, start, end } of located) {
		for (let pageStart = start; pageStart <= end; pageStart += 50) {
			const page = await bridgePostJson('/history/page', { chat_id: chatId, start: pageStart, end: Math.min(end, pageStart + 49) }, 60_000);
			for (const payload of Array.isArray(page?.messages) ? page.messages : []) {
				const row = historyRow(payload, { normalize: normalizeLarkAgentXRelayMessage, describeUnsupported: normalizeLarkAgentXUnsupportedMessage, isDegradedCard: larkAgentXCardIsDegraded });
				// Positions bracket the range; the time check is what decides.
				if (row && row.source_create_time >= range.fromMs && row.source_create_time <= range.toMs) rows.push(row);
			}
		}
	}
	return rows;
}

async function handleRouteHistoryBundle(request, response, url, sourceKey) {
	const fail = (status, message) => {
		response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'error', message }));
	};
	let range;
	let route;
	let rows;
	let chatIds;
	const source = String(url.searchParams.get('source') ?? 'full').trim().toLowerCase();
	if (!['full', 'relayed'].includes(source)) { fail(400, 'source 只支持 full（完整历史）或 relayed（仅已转发）'); return; }
	try {
		range = bundleRange(url.searchParams.get('from'), url.searchParams.get('to'));
	} catch (error) {
		fail(400, error instanceof Error ? error.message : String(error));
		return;
	}
	try {
		const routes = await ledger.relayRoutes();
		route = routes.find((item) => item.key === sourceKey);
		if (!route) { fail(404, '源群不存在'); return; }
		chatIds = await routeExportChatIds(route, routes, await exportNumericIdsByRoute(routes));
		rows = await ledger.relayMessagesByChatIdsWindow(chatIds, range.fromMs, range.toMs, MAX_BUNDLE_MESSAGES + 1);
	} catch (error) {
		fail(503, `读取转发账本失败：${error instanceof Error ? error.message : String(error)}`);
		return;
	}
	if (rows.length > MAX_BUNDLE_MESSAGES) { fail(400, `该时间范围内消息超过 ${MAX_BUNDLE_MESSAGES} 条，请缩小范围后分段导出`); return; }
	const base = bundleBaseName(route.chatName, range.from, range.to);
	const dryRun = url.searchParams.get('dry_run') === '1';
	let historyPositions = null;
	let historyOnly = 0;
	// The ledger recorded some messages under both id forms while both
	// transports ran; one export should hold each once, whichever source it is.
	rows = mergeHistoryRows(rows, []).rows;
	const relayedCount = rows.length;
	let historyOrigin = 'live';
	let historyReason = '';
	if (source === 'full') {
		// A group the account has left cannot be read any more; its stored copy can.
		const states = (await ledger.groupChatStates().catch(() => [])).filter((state) => chatIds.includes(String(state.chat_id)));
		const gone = states.length > 0 && states.every((state) => state.membership !== 'member');
		try {
			if (gone) throw new Error('账号已不在该群');
			const located = await locateGroupHistory(chatIds, range);
			historyPositions = located.reduce((total, item) => total + item.count, 0);
			// The preview only needs the size of the range; reading every page is for the real export.
			if (!dryRun) {
				const merged = mergeHistoryRows(rows, await readGroupHistory(located, range));
				rows = merged.rows;
				historyOnly = merged.from_history_only;
				if (rows.length > MAX_BUNDLE_MESSAGES) { fail(400, `该时间范围内消息超过 ${MAX_BUNDLE_MESSAGES} 条，请缩小范围后分段导出`); return; }
			}
		} catch (error) {
			historyReason = error instanceof Error ? error.message : String(error);
			const stored = await ledger.groupHistoryRows(chatIds, range.fromMs, range.toMs, MAX_BUNDLE_MESSAGES + 1).catch(() => []);
			historyOrigin = historyFallback({ gone, storedCount: stored.length, relayedCount: rows.length });
			// Fail rather than quietly export less than was asked for.
			if (historyOrigin === 'fail') { fail(502, `读取群历史失败：${historyReason}，也没有已保存的群历史。可以改选“仅已转发”导出。`); return; }
			historyPositions = stored.length;
			const merged = mergeHistoryRows(rows, stored);
			rows = merged.rows;
			historyOnly = merged.from_history_only;
			if (rows.length > MAX_BUNDLE_MESSAGES) { fail(400, `该时间范围内消息超过 ${MAX_BUNDLE_MESSAGES} 条，请缩小范围后分段导出`); return; }
		}
	}
	// The preview needs the picture count but not the names (one gateway call
	// per unknown sender), so names are resolved only for the real export.
	const context = await bundleContext(rows, { names: !dryRun });
	const entries = bundleEntries(rows, context);
	if (dryRun) {
		// Counts first, so the dashboard can say what is about to be downloaded.
		response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({
			chat_name: route.chatName, from: range.from, to: range.to, filename: `${base}.zip`, chat_ids: chatIds,
			source, relayed_messages: relayedCount, history_positions: historyPositions, history_origin: historyOrigin, history_reason: historyReason || null,
			messages: entries.length, images: entries.reduce((total, entry) => total + entryImageCount(entry), 0),
			first_message_at: entries.length ? new Date(entries[0].time_ms).toISOString() : null,
			last_message_at: entries.length ? new Date(entries.at(-1).time_ms).toISOString() : null,
		}));
		return;
	}
	response.writeHead(200, {
		'content-type': 'application/zip',
		// An ASCII fallback plus the RFC 5987 form: group names are Chinese.
		'content-disposition': `attachment; filename="group-history_${range.from}_${range.to}.zip"; filename*=UTF-8''${encodeURIComponent(`${base}.zip`)}`,
		'cache-control': 'no-store',
		'x-larkagentx-event-count': String(entries.length),
	});
	try {
		const summary = await writeHistoryBundle({
			title: route.chatName, from: range.from, to: range.to, rows, fetchImage: fetchBundleImage,
			cardImages: context.cardImages, senderNames: context.senderNames,
			note: historySourceNote({ source, origin: historyOrigin, reason: historyReason, historyOnly, total: rows.length }),
			write: (chunk) => writeChunk(response, chunk),
		});
		console.log(`历史资料包已导出 mode=${source} history_only=${historyOnly} source=${route.key} range=${range.from}..${range.to} messages=${summary.messages} images=${summary.images} failed_images=${summary.failed_images} bytes=${summary.bytes}`);
		await endWritable(response);
	} catch (error) {
		// Headers are gone; a truncated archive is the only honest signal left.
		console.error(`历史资料包导出中断 source=${route.key}: ${error instanceof Error ? error.message : String(error)}`);
		response.destroy(error instanceof Error ? error : new Error(String(error)));
	}
}

async function handleLarkAgentXSend(request, response, action) {
	// Unified outbound text send: the adapter injects the bridge token; the
	// bridge owns the whitelist, token bucket, idempotency and audit trail, so
	// 429/409 and their bodies pass through unchanged.
	if (!larkAgentXIngressToken || !larkAgentXHealthUrl) {
		response.writeHead(503, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'error', message: 'LarkAgentX 发送服务未配置' }));
		return;
	}
	try {
		const bridgeUrl = new URL(larkAgentXHealthUrl);
		let init;
		if (action === 'send') {
			const payload = await readJsonBody(request, 16 * 1024);
			bridgeUrl.pathname = '/send';
			bridgeUrl.search = '';
			init = {
				method: 'POST',
				headers: { 'x-larkagentx-token': larkAgentXIngressToken, 'content-type': 'application/json' },
				body: JSON.stringify({
					chat_id: String(payload?.chat_id ?? ''), text: String(payload?.text ?? ''),
					root_id: String(payload?.root_id ?? ''), idempotency_key: String(payload?.idempotency_key ?? ''),
				}),
			};
		} else {
			const limit = Math.max(1, Math.min(500, Number(new URL(request.url, 'http://x').searchParams.get('limit')) || 50));
			const chatId = String(new URL(request.url, 'http://x').searchParams.get('chat_id') ?? '').slice(0, 128);
			bridgeUrl.pathname = '/send/audit';
			bridgeUrl.search = '';
			bridgeUrl.searchParams.set('limit', String(limit));
			if (chatId) bridgeUrl.searchParams.set('chat_id', chatId);
			init = { headers: { 'x-larkagentx-token': larkAgentXIngressToken } };
		}
		const upstream = await fetch(bridgeUrl, { ...init, signal: AbortSignal.timeout(30_000) });
		const text = await upstream.text();
		const headers = { 'content-type': 'application/json', 'cache-control': 'no-store' };
		const retryAfter = upstream.headers.get('retry-after');
		if (retryAfter) headers['retry-after'] = retryAfter;
		response.writeHead(upstream.status === 401 ? 502 : upstream.status, headers);
		response.end(text || JSON.stringify({ status: upstream.ok ? 'ok' : 'error' }));
	} catch (error) {
		if (!response.headersSent) response.writeHead(502, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		if (!response.writableEnded) response.end(JSON.stringify({ status: 'error', message: `LarkAgentX 发送不可用：${error instanceof Error ? error.message : String(error)}` }));
	}
}

async function handleLarkAgentXQr(request, response, action) {
	// Browser-facing QR login: the adapter holds the bridge token and injects
	// it, so the operator's browser never needs a secret. init opens a login
	// session (returns a rendered QR PNG + token); poll reports its status and,
	// on a successful scan, the bridge persists the session and reconnects.
	if (!larkAgentXIngressToken || !larkAgentXHealthUrl) {
		response.writeHead(503, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'error', message: 'LarkAgentX 登录服务未配置' }));
		return;
	}
	try {
		const bridgeUrl = new URL(larkAgentXHealthUrl);
		bridgeUrl.pathname = action === 'init' ? '/qr/init' : '/qr/poll';
		bridgeUrl.search = '';
		let body = '{}';
		if (action === 'poll') {
			const payload = await readJsonBody(request, 4 * 1024);
			const sessionId = String(payload?.session_id ?? '').trim();
			if (!sessionId || sessionId.length > 128) throw new Error('session_id 不能为空');
			body = JSON.stringify({ session_id: sessionId });
		}
		const upstream = await fetch(bridgeUrl, {
			method: 'POST',
			headers: { 'x-larkagentx-token': larkAgentXIngressToken, 'content-type': 'application/json' },
			body,
			signal: AbortSignal.timeout(20_000),
		});
		const text = await upstream.text();
		response.writeHead(upstream.ok ? 200 : (upstream.status === 401 ? 502 : upstream.status), { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(text || JSON.stringify({ status: upstream.ok ? 'ok' : 'error' }));
	} catch (error) {
		if (!response.headersSent) response.writeHead(502, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		if (!response.writableEnded) response.end(JSON.stringify({ status: 'error', message: `LarkAgentX 登录不可用：${error instanceof Error ? error.message : String(error)}` }));
	}
}

function publicRelayRoute(route) {
	return { key: route.key, chat_name: route.chatName, tag: route.tag, target_chat_ids: route.targetChatIds ?? [], enabled: route.enabled !== false, archived: Boolean(route.archivedAt), archived_reason: route.archivedReason ?? null, created_at: asIsoString(route.created_at), updated_at: asIsoString(route.updated_at) };
}

function relayRouteTag(value) {
	const tag = String(value ?? '').trim().replace(/^#/, '').toLowerCase();
	if (!/^[a-z0-9][a-z0-9_-]{0,31}$/.test(tag)) throw new Error('标签必须为 1–32 位小写字母、数字、- 或 _，且不能以符号开头');
	return tag;
}

async function resolveRelayRouteInput(payload, current = null) {
	const chatName = String(payload?.chat_name ?? current?.chatName ?? '').trim();
	if (!chatName || chatName.length > 120) throw new Error('请填写 1–120 字的源群名称');
	const tag = relayRouteTag(payload?.tag ?? current?.tag);
	const enabled = payload?.enabled === undefined ? current?.enabled !== false : payload.enabled !== false;
	const allowPending = payload?.allow_pending === true;
	const requestedChatId = String(payload?.chat_id ?? '').trim();
	if (requestedChatId && !/^oc_[A-Za-z0-9]+$/.test(requestedChatId)) throw new Error('chat_id 格式无效');
	// A group picked from the joined list comes with its WebSocket id.  That id
	// is all the relay needs to listen, so nothing is looked up through OAuth.
	const websocketChatId = String(payload?.websocket_chat_id ?? '').trim();
	if (websocketChatId && !/^\d{6,24}$/.test(websocketChatId)) throw new Error('websocket_chat_id 必须是数字群 ID');
	const rawTargets = payload?.target_chat_ids === undefined ? current?.targetChatIds ?? [] : payload.target_chat_ids;
	if (!Array.isArray(rawTargets) || rawTargets.length > 8) throw new Error('目标群必须是至多 8 个 chat_id 的数组');
	const targetChatIds = [...new Set(rawTargets.map((value) => String(value ?? '').trim()).filter(Boolean))];
	if (targetChatIds.some((value) => !/^oc_[A-Za-z0-9]+$/.test(value) && !(WEBHOOK_ONLY_TARGET_ID.test(value) && webhookTargets.has(value)))) throw new Error('目标群 chat_id 格式无效，或该目标群的 webhook 已被删除');
	const rawTargetNames = payload?.target_chat_names === undefined ? [] : payload.target_chat_names;
	if (!Array.isArray(rawTargetNames) || rawTargetNames.length > 8) throw new Error('目标群名称必须是至多 8 个群名的数组');
	const targetChatNames = [...new Set(rawTargetNames.map((value) => String(value ?? '').trim()).filter(Boolean))];
	if (targetChatNames.some((value) => value.length > 120)) throw new Error('目标群名称不能超过 120 字');
	async function resolveChatName(name) {
		let exact = [];
		try {
			const result = await feishuUserOauth.sourceApi.chatSearch(name);
			exact = (result.data?.items ?? []).filter((chat) => chat.name === name);
		} catch {
			// Fall through to the paginated chat list when search is restricted.
		}
		if (!exact.length) {
			let pageToken = '';
			for (let page = 0; page < 20; page++) {
				const result = await feishuUserOauth.sourceApi.chatList({ ...(pageToken ? { page_token: pageToken } : {}) });
				exact.push(...(result.data?.items ?? []).filter((chat) => chat.name === name));
				if (!result.data?.has_more || !result.data?.page_token || exact.length) break;
				pageToken = result.data.page_token;
			}
		}
		if (!exact.length) throw new Error(`未找到可读取的群“${name}”；请确认用户 OAuth 能看到该群`);
		if (exact.length > 1) throw new Error(`找到多个同名群“${name}”，请改用 chat_id`);
		return exact[0].chat_id;
	}
	for (const name of targetChatNames) targetChatIds.push(await resolveChatName(name));
	const uniqueTargetChatIds = [...new Set(targetChatIds)];
	if (uniqueTargetChatIds.length > 8) throw new Error('目标群总数不能超过 8 个');
	if (current && chatName === current.chatName && !requestedChatId) return { chatId: current.chatId, chatName, tag, targetChatIds: uniqueTargetChatIds, enabled };
	if (websocketChatId) return { chatId: requestedChatId, chatName, tag, targetChatIds: uniqueTargetChatIds, enabled };
	// A known chat ID is authoritative. Do not require source group search first:
	// user OAuth can read a particular external chat while search/list visibility
	// is administratively restricted.
	if (requestedChatId) return { chatId: requestedChatId, chatName, tag, targetChatIds: uniqueTargetChatIds, enabled };
	let exact = [];
	try {
		const result = await feishuUserOauth.sourceApi.chatSearch(chatName);
		exact = (result.data?.items ?? []).filter((chat) => chat.name === chatName);
	} catch {
		// Try the granted chat-list permission below before reporting a route error.
	}
	if (!exact.length) {
		let pageToken = '';
		try {
			for (let page = 0; page < 20; page++) {
				const result = await feishuUserOauth.sourceApi.chatList({ ...(pageToken ? { page_token: pageToken } : {}) });
				exact.push(...(result.data?.items ?? []).filter((chat) => chat.name === chatName));
				if (!result.data?.has_more || !result.data?.page_token || exact.length) break;
				pageToken = result.data.page_token;
			}
		} catch (error) {
			if (allowPending) return { chatId: '', chatName, tag, targetChatIds: uniqueTargetChatIds, enabled };
			throw error;
		}
	}
	if (!exact.length) {
		if (allowPending) return { chatId: '', chatName, tag, targetChatIds: uniqueTargetChatIds, enabled };
		throw new Error(`未找到可读取的群“${chatName}”；可填写已知 chat_id 直接注册`);
	}
	if (exact.length > 1) throw new Error(`找到多个同名群“${chatName}”，请填写 chat_id 后再保存`);
	return { chatId: exact[0].chat_id, chatName: exact[0].name, tag, targetChatIds: uniqueTargetChatIds, enabled };
}

async function createRelayRoute(payload) {
	const route = await resolveRelayRouteInput(payload);
	await refuseGroupBoundElsewhere(payload?.websocket_chat_id, null);
	const created = await ledger.createRelayRoute({ sourceKey: `relay_${randomUUID().replaceAll('-', '')}`, ...route });
	await applyDownstreamChoice(created, payload, true);
	return created;
}

// Whether the route's tag feeds downstream research data.  A new source group
// does unless the request says otherwise (user rule of 2026-10-11); an edit
// changes it only when the request says so.  The tag is registered here, so a
// tag never used before needs no registry edit and no release.
async function applyDownstreamChoice(route, payload, isNew) {
	const choice = payload?.ingest === undefined ? (isNew ? true : null) : payload.ingest !== false;
	if (choice === null) return;
	if (choice) {
		const registered = await sourceTags.register({ tag: route.tag, label: String(payload?.label ?? '').trim() || route.chatName });
		if (registered.created) console.log(`来源标签 #${route.tag} 已登记进下游研究数据（来自看板，源群 ${route.chatName}）`);
	} else {
		await sourceTags.setEnabled(route.tag, false);
	}
}

// One group feeds one route.  Checked before the route is saved, so a refusal
// does not leave a route behind that can never be bound.
async function refuseGroupBoundElsewhere(websocketChatId, ownKey) {
	const chatId = String(websocketChatId ?? '').trim();
	if (!chatId) return;
	const joined = await joinedGroupsWithRoutes(false).catch(() => null);
	const holder = [...(joined?.bound ?? []), ...(joined?.archived ?? [])].find((item) => String(item.chat_id) === chatId && item.source_key !== ownKey);
	if (!holder) return;
	const route = (await ledger.relayRoutes()).find((item) => item.key === holder.source_key);
	throw new Error(`该群已经绑定到路由 #${route?.tag ?? holder.source_key}（${route?.chatName ?? holder.chat_name ?? ''}），不能重复绑定`);
}

// Bind a saved route and have the directory re-read at once, so its
// membership row exists and its history starts being read without a wait.
async function bindRelayRoute(route, payload = null) {
	const binding = await routeBinder.bind(route, { websocketChatId: String(payload?.websocket_chat_id ?? '').trim() });
	groupSync.requestRefresh();
	void runGroupSync();
	return binding;
}

async function updateRelayRoute(sourceKey, payload) {
	const current = (await ledger.relayRoutes()).find((route) => route.key === sourceKey);
	if (!current) return null;
	const route = await resolveRelayRouteInput(payload, current);
	await refuseGroupBoundElsewhere(payload?.websocket_chat_id, sourceKey);
	// Before the route is saved: a refused choice must not leave a half-applied edit.
	await applyDownstreamChoice(route, payload, false);
	return ledger.updateRelayRoute(sourceKey, route);
}


async function proxyResearch(path, search, response) {
	if (!quantServiceUrl) throw new Error('量化研究服务未配置');
	// 30 s: on 2026-10-09 the owner's minute writes queued reads that take 0.2-7 s alone past 15 s,
	// and the console showed them as unavailable. The post-close report keeps its longer budget.
	// The intraday temperature's first read of a session may read up to 49 minute documents (decision 0013).
	const readTimeoutMs = path === '/api/v1/strategy/post-close/latest' || path === '/api/v1/market/temperature/intraday' ? 45_000 : 30_000;
	const upstream = await fetch(`${quantServiceUrl}${path}${search}`, { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(readTimeoutMs) });
	const body = await upstream.text();
	response.writeHead(upstream.status, { 'content-type': upstream.headers.get('content-type') ?? 'application/json', 'cache-control': 'no-store' });
	response.end(body);
}

async function proxyResearchAction(path, request, response, method = 'POST') {
	if (!quantServiceUrl) throw new Error('量化研究服务未配置');
	const chunks = []; let size = 0;
	for await (const chunk of request) {
		size += chunk.length;
		if (size > 64 * 1024) throw new Error('研究操作请求超过 64 KiB 上限');
		chunks.push(chunk);
	}
	const longRunning = path.includes('/market/') || path.includes('/akshare/probe') || path.includes('/strategy/post-close/run') || path.includes('/strategy/pattern-mining/run') || path.includes('/strategy/watchlist-main-wave/run') || path.includes('/ten-day-leader-rotation/run');
	const timeoutMs = path.includes('/market/post-close/refresh') ? 360_000 : longRunning ? 180_000 : 45_000;
	const upstream = await fetch(`${quantServiceUrl}${path}`, {
		method,
		headers: {
			'content-type': 'application/json', accept: 'application/json',
			...(quantWriteApiKey ? { 'X-Quant-Write-Key': quantWriteApiKey } : {}),
		},
		body: Buffer.concat(chunks), signal: AbortSignal.timeout(timeoutMs),
	});
	const body = await upstream.text();
	response.writeHead(upstream.status, { 'content-type': upstream.headers.get('content-type') ?? 'application/json', 'cache-control': 'no-store' });
	response.end(body);
}

const dashboard = createServer((request, response) => {
	const url = new URL(request.url ?? '/', 'http://localhost');
	const crossSite = crossSiteWriteRejection(request);
	if (crossSite) {
		response.writeHead(403, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'forbidden', reason: crossSite }));
		return;
	}
	const research = matchResearchRoute(request.method, url.pathname);
	if (research) {
		const proxied = research.kind === 'read'
			? proxyResearch(research.upstream, url.search, response)
			: proxyResearchAction(research.upstream, request, response, research.method);
		void proxied.catch((error) => {
			response.writeHead(503, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) }));
		});
		return;
	}
	if (url.pathname === '/api/config' && request.method === 'GET') {
		response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ routes: [...sourceRoutes.values()].filter((route) => route.enabled !== false).map(({ tag, label, topic_key, publisher_key }) => ({ tag, label, topic_key, publisher_key })) }));
		return;
	}
	if (url.pathname.startsWith('/api/jobs/') && request.method === 'GET') {
		void ledger.getJob(url.pathname.slice('/api/jobs/'.length)).then((job) => { if (!job) { response.writeHead(404).end(); return; } response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify({ job })); }).catch((error) => response.writeHead(503).end(String(error)));
		return;
	}
	if (url.pathname.startsWith('/api/assets/') && url.pathname.endsWith('/parts') && request.method === 'GET') {
		const assetId = url.pathname.slice('/api/assets/'.length, -'/parts'.length);
		void ledger.assetParts(assetId).then((parts) => response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }).end(JSON.stringify({ parts }))).catch((error) => response.writeHead(503).end(String(error)));
		return;
	}
	if (url.pathname.startsWith('/api/jobs/') && url.pathname.endsWith('/retry') && request.method === 'POST') {
		const jobId = url.pathname.slice('/api/jobs/'.length, -'/retry'.length);
		void ledger.retryJob(jobId).then((job) => { if (!job) { response.writeHead(409, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: '只有失败、重复或卡住的排队任务可以手动重试' })); return; } response.writeHead(202, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'queued', job_id: job.job_id, message: '已进入本地重试队列；不会自动创建重复远端请求' })); }).catch((error) => response.writeHead(503).end(String(error)));
		return;
	}
	if (url.pathname === '/api/group-relay/status' && request.method === 'GET') {
		void groupRelayDashboardStatus().then((status) => {
			response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify(status));
		}).catch((error) => {
			response.writeHead(503, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) }));
		});
		return;
	}
	if (url.pathname === '/api/group-relay/larkagentx/history/export' && request.method === 'GET') {
		void handleLarkAgentXHistoryExport(request, response, url);
		return;
	}
	if (url.pathname === '/api/group-relay/larkagentx/oauth/authorize-url' && request.method === 'GET') {
		try {
			const state = String(url.searchParams.get('state') ?? '').slice(0, 200);
			response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify(feishuUserOauth.authorizeUrl(state)));
		} catch (error) {
			response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) }));
		}
		return;
	}
	if (url.pathname === '/api/group-relay/larkagentx/oauth/exchange' && request.method === 'POST') {
		// The adapter holds FEISHU_APP_SECRET and exchanges the code itself, so
		// the browser never carries a secret (unlike /internal/feishu-user-oauth,
		// which the CLI reaches with the quant write key).
		void (async () => {
			try {
				const payload = await readJsonBody(request, 16 * 1024);
				const code = String(payload?.authorization_code ?? '').trim();
				if (!code) throw new Error('authorization_code 不能为空');
				const token = await feishuUserOauth.exchangeAuthorizationCode(code, payload?.redirect_uri);
				response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
				response.end(JSON.stringify({ status: 'stored', refresh_expires_at: token.refresh_expires_at, scopes: token.scopes }));
			} catch (error) {
				response.writeHead(400, { 'content-type': 'application/json', 'cache-control': 'no-store' });
				response.end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) }));
			}
		})();
		return;
	}
	if (url.pathname === '/api/group-relay/larkagentx/send' && request.method === 'POST') {
		void handleLarkAgentXSend(request, response, 'send');
		return;
	}
	if (url.pathname === '/api/group-relay/larkagentx/send/audit' && request.method === 'GET') {
		void handleLarkAgentXSend(request, response, 'audit');
		return;
	}
	if (url.pathname === '/api/group-relay/larkagentx/qr/init' && request.method === 'POST') {
		void handleLarkAgentXQr(request, response, 'init');
		return;
	}
	if (url.pathname === '/api/group-relay/larkagentx/qr/poll' && request.method === 'POST') {
		void handleLarkAgentXQr(request, response, 'poll');
		return;
	}
	if (url.pathname === '/api/group-relay/larkagentx/export-bookmarks' && request.method === 'GET') {
		void ledger.listExportBookmarks()
			.then((bookmarks) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify({ bookmarks })); })
			.catch((error) => { response.writeHead(503, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })); });
		return;
	}
	if (url.pathname === '/api/group-relay/itougu/refresh' && request.method === 'POST') {
		void triggerItouguRefresh().then(({ http_status, payload }) => {
			response.writeHead(http_status === 200 ? 200 : http_status === 409 ? 409 : 502, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify(payload));
		}).catch((error) => {
			response.writeHead(502, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) }));
		});
		return;
	}
	if (url.pathname === '/api/group-relay/chats' && request.method === 'GET') {
		// The groups the account is in, for picking a source group, with what is
		// already bound.  When the bridge cannot be read the stored state is still
		// returned, with the reason, so the page shows what it last knew.
		void (async () => {
			let live = null;
			let error = null;
			try { live = await joinedGroupsWithRoutes(url.searchParams.get('refresh') === '1'); }
			catch (cause) { error = cause instanceof Error ? cause.message : String(cause); }
			if (live?.groups) await ledger.recordJoinedGroups(live.groups).catch(() => {});
			const [states, routes] = await Promise.all([ledger.groupChatStates(), ledger.relayRoutes()]);
			const directory = live ?? groupSync.directory();
			response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({
				observed_at: new Date().toISOString(), live: Boolean(live), error,
				groups: describeGroups({ groups: directory.groups ?? [], bound: directory.bound ?? [], archived: directory.archived ?? [], states, routes }),
				sync: groupSync.status(),
			}));
		})().catch((error) => response.writeHead(503, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/group-relay/targets' && request.method === 'GET') {
		response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ targets: webhookTargets.list() }));
		return;
	}
	if (url.pathname === '/api/group-relay/targets' && request.method === 'POST') {
		// Saving sends one test message to the group through the webhook; a
		// webhook that refuses it is not stored.  Without a webhook address the
		// group's relay bot is created (or found) through the account's session.
		void readJsonBody(request, 16 * 1024).then(async (payload) => {
			const typed = String(payload?.webhook_url ?? '').trim();
			const target = typed
				? await webhookTargets.add({ name: payload?.name, webhookUrl: typed, keyword: payload?.keyword, websocketChatId: payload?.websocket_chat_id, chatId: payload?.chat_id })
				: await webhookTargets.addManaged({ name: payload?.name, websocketChatId: payload?.websocket_chat_id, keyword: payload?.keyword });
			console.log(`目标群 ${target.name}（${target.chat_id}）的 webhook 已由看板配置并通过测试${typed ? '' : target.bot_created ? '；机器人为自动创建' : '；沿用已有的转发机器人'}`);
			response.writeHead(201, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({ target }));
		}).catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	const relayTargetMatch = /^\/api\/group-relay\/targets\/([A-Za-z0-9_-]+)$/.exec(url.pathname);
	if (relayTargetMatch && request.method === 'DELETE') {
		void ledger.relayRoutes().then(async (routes) => {
			const removed = await webhookTargets.remove(relayTargetMatch[1], { routes });
			if (!removed) { response.writeHead(404, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: '目标群不存在' })); return; }
			response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }).end(JSON.stringify({ status: 'removed', bot_removed: removed.bot_removed }));
		}).catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/group-relay/routes' && request.method === 'GET') {
		void ledger.relayRoutes().then((routes) => {
			response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({ routes: routes.map(publicRelayRoute) }));
		}).catch((error) => response.writeHead(503, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/group-relay/routes' && request.method === 'POST') {
		void readJsonBody(request, 16 * 1024).then(async (payload) => {
			const route = await createRelayRoute(payload);
			const websocketBinding = await bindRelayRoute(route, payload);
			response.writeHead(201, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({ route: publicRelayRoute(route), websocket_binding: websocketBinding }));
		}).catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	const relayRouteMatch = /^\/api\/group-relay\/routes\/([A-Za-z0-9_-]+)$/.exec(url.pathname);
	if (relayRouteMatch && request.method === 'PUT') {
		void readJsonBody(request, 16 * 1024).then(async (payload) => {
			const route = await updateRelayRoute(relayRouteMatch[1], payload);
			if (!route) { response.writeHead(404, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: '源群不存在' })); return; }
			return bindRelayRoute(route, payload).then((websocketBinding) => {
				response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
				response.end(JSON.stringify({ route: publicRelayRoute(route), websocket_binding: websocketBinding }));
			});
		}).catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	const relayRouteBundleMatch = /^\/api\/group-relay\/routes\/([A-Za-z0-9_-]+)\/bundle$/.exec(url.pathname);
	if (relayRouteBundleMatch && request.method === 'GET') {
		void handleRouteHistoryBundle(request, response, url, relayRouteBundleMatch[1]);
		return;
	}
	const relayRouteBindMatch = /^\/api\/group-relay\/routes\/([A-Za-z0-9_-]+)\/bind$/.exec(url.pathname);
	if (relayRouteBindMatch && request.method === 'POST') {
		// Manual retry: bind an already-saved route (for example after a login
		// expiry made the first attempt fail).
		void ledger.relayRoutes().then(async (routes) => {
			const route = routes.find((item) => item.key === relayRouteBindMatch[1]);
			if (!route) { response.writeHead(404, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: '源群不存在' })); return; }
			const websocketBinding = await bindRelayRoute(route);
			response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({ route: publicRelayRoute(route), websocket_binding: websocketBinding }));
		}).catch((error) => response.writeHead(503, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (relayRouteMatch && request.method === 'DELETE') {
		void removeRelayRoute(relayRouteMatch[1]).then(async (deleted) => {
			if (!deleted) { response.writeHead(404, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: '源群不存在' })); return; }
			groupSync.requestRefresh();
			response.writeHead(204, { 'cache-control': 'no-store' }).end();
		}).catch((error) => response.writeHead(503, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/feishu-workbench/status' && request.method === 'GET') {
		void Promise.all([feishuWorkbench.status(), feishuUserOauth.status()]).then(([workbench, oauth]) => {
			response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({ ...workbench, capabilities: annotateWorkbenchCapabilities(workbench.capabilities, oauth, workbench.application_inspection), user_oauth_configured: Boolean(oauth.configured), user_oauth_scopes: oauth.scopes ?? '', user_oauth_scope_audit: oauth.scope_audit ?? null, event_subscriptions: workbenchEventStatus() }));
		}).catch((error) => response.writeHead(503, { 'content-type': 'application/json', 'cache-control': 'no-store' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/baidu-pan/status' && request.method === 'GET') {
		void baiduPan.status().then((status) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(status)); })
			.catch((error) => response.writeHead(503, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/baidu-pan/market-archive/status' && request.method === 'GET') {
		void baiduPanMarketArchive.status().then((status) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(status)); })
			.catch((error) => response.writeHead(503, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/baidu-pan/oauth/url' && request.method === 'GET') {
		try { const state = String(url.searchParams.get('state') ?? 'baidu-pan').slice(0, 200); response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify({ authorization_url: baiduPan.authorizationUrl(state), redirect_uri: String(process.env.BAIDU_PAN_REDIRECT_URI ?? 'oob') })); } catch (error) { response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })); }
		return;
	}
	if (url.pathname === '/api/baidu-pan/oauth/device' && request.method === 'POST') {
		void baiduPan.deviceCode().then((result) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(result)); })
			.catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/baidu-pan/oauth/exchange' && request.method === 'POST') {
		void readJsonBody(request, 16 * 1024).then((payload) => baiduPan.exchangeAuthorizationCode(payload?.code, payload?.redirect_uri)).then((status) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(status)); })
			.catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/baidu-pan/oauth/device-exchange' && request.method === 'POST') {
		void readJsonBody(request, 16 * 1024).then((payload) => baiduPan.exchangeDeviceCode(payload?.device_code ?? payload?.code)).then((status) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(status)); })
			.catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/baidu-pan/oauth/bootstrap' && request.method === 'POST') {
		void readJsonBody(request, 16 * 1024).then((payload) => baiduPan.bootstrapRefreshToken(payload?.refresh_token)).then((status) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(status)); })
			.catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/baidu-pan/oauth/refresh' && request.method === 'POST') {
		void baiduPan.refresh().then((status) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(status)); })
			.catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/baidu-pan/user' && request.method === 'GET') {
		void baiduPan.userInfo().then((result) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(result)); })
			.catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/baidu-pan/device-user' && request.method === 'GET') {
		void baiduPan.iotQueryUserInfo(url.searchParams.get('device_id')).then((result) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(result)); })
			.catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/baidu-pan/quota' && request.method === 'GET') {
		void baiduPan.quota({ checkFree: url.searchParams.has('checkfree') ? url.searchParams.get('checkfree') : undefined, checkExpire: url.searchParams.has('checkexpire') ? url.searchParams.get('checkexpire') : undefined }).then((result) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(result)); })
			.catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/baidu-pan/files' && request.method === 'GET') {
		const dir = url.searchParams.get('dir') ?? '/'; const type = url.searchParams.get('type') ?? 'list'; const options = { dir, start: url.searchParams.get('start') ?? 0, limit: url.searchParams.get('limit') ?? 1000 };
		const method = type === 'doc' ? baiduPan.fileDocList : type === 'image' ? baiduPan.fileImageList : type === 'video' ? baiduPan.fileVideoList : baiduPan.list;
		void method(options).then((result) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(result)); })
			.catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/baidu-pan/search' && request.method === 'GET') {
		const query = url.searchParams.get('q') ?? ''; const semantic = url.searchParams.get('semantic') === 'true';
		void (semantic ? baiduPan.semanticSearch(query, { dir: url.searchParams.get('dir') ?? undefined }) : baiduPan.search(query, { dir: url.searchParams.get('dir') ?? '/' })).then((result) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(result)); })
			.catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/baidu-pan/meta' && request.method === 'GET') {
		const rawIds = url.searchParams.getAll('fsid').length ? url.searchParams.getAll('fsid') : String(url.searchParams.get('fsids') ?? '').split(',').map((value) => value.trim()).filter(Boolean);
		void baiduPan.fileMeta(rawIds).then((result) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(result)); })
			.catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/baidu-pan/list-all' && request.method === 'GET') {
		void baiduPan.listAll({ path: url.searchParams.get('path') ?? '/', recursion: url.searchParams.get('recursion') ?? 1, web: url.searchParams.get('web') ?? undefined, start: url.searchParams.get('start') ?? undefined, limit: url.searchParams.get('limit') ?? undefined, order: url.searchParams.get('order') ?? undefined, desc: url.searchParams.get('desc') ?? undefined }).then((result) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(result)); })
			.catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/baidu-pan/manage' && request.method === 'POST') {
		void readJsonBody(request, 32 * 1024).then(async (payload) => {
			const operation = String(payload?.operation ?? '').trim();
			if (operation === 'mkdir') return baiduPan.mkdir(payload?.path);
			if (operation === 'copy') return baiduPan.copy(payload?.from, payload?.to, payload?.name);
			if (operation === 'move') return baiduPan.move(payload?.from, payload?.to, payload?.name);
			if (operation === 'rename') return baiduPan.rename(payload?.path, payload?.name);
			if (operation === 'delete') return baiduPan.remove(payload?.path);
			throw new Error('不支持的百度网盘文件操作');
		}).then((result) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(result)); })
			.catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/baidu-pan/share' && request.method === 'POST') {
		void readJsonBody(request, 16 * 1024).then((payload) => baiduPan.createShareLink(payload?.fsids ?? payload?.fsid_list, { period: payload?.period, password: payload?.password })).then((result) => { response.writeHead(201, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify(result)); })
			.catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/feishu-workbench/application-inspection' && request.method === 'POST') {
		void feishuWorkbench.inspectApplication().then((inspection) => {
			response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({ inspection }));
		}).catch((error) => response.writeHead(503, { 'content-type': 'application/json', 'cache-control': 'no-store' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/feishu-workbench/messages' && request.method === 'GET') {
		const limit = Number(url.searchParams.get('limit') ?? 50);
		void ledger.recentRelayMessages(limit).then((items) => {
			response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({ items }));
		}).catch((error) => response.writeHead(503, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/feishu-workbench/actions' && request.method === 'POST') {
		void readJsonBody(request, 32 * 1024).then((payload) => feishuWorkbench.performAction({
			sourceMessageId: String(payload?.source_message_id ?? ''), action: String(payload?.action ?? ''),
			operatorOpenId: String(payload?.operator_open_id ?? ''), operatorName: String(payload?.operator_name ?? ''),
		})).then((result) => {
			response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({ status: 'ok', record: result.record, external: result.external ?? null }));
		}).catch((error) => response.writeHead(400, { 'content-type': 'application/json', 'cache-control': 'no-store' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/feishu-workbench/documents' && request.method === 'POST') {
		void readJsonBody(request, 64 * 1024).then((payload) => feishuWorkbench.createDocument(payload ?? {})).then((result) => {
			response.writeHead(201, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify({ status: 'created', result }));
		}).catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/feishu-workbench/base-records' && request.method === 'POST') {
		void readJsonBody(request, 128 * 1024).then((payload) => feishuWorkbench.createBaseRecord(payload ?? {})).then((result) => {
			response.writeHead(201, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify({ status: 'created', result }));
		}).catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/feishu-workbench/base-records' && request.method === 'GET') {
		void feishuWorkbench.listBaseRecords({ pageSize: url.searchParams.get('page_size'), pageToken: url.searchParams.get('page_token') ?? '' }).then((result) => {
			response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify({ status: 'ok', result }));
		}).catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	const baseRecordMatch = url.pathname.match(/^\/api\/feishu-workbench\/base-records\/([^/]+)$/);
	if (baseRecordMatch && request.method === 'PUT') {
		void readJsonBody(request, 128 * 1024).then((payload) => feishuWorkbench.updateBaseRecord({ ...(payload ?? {}), recordId: decodeURIComponent(baseRecordMatch[1]) })).then((result) => {
			response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify({ status: 'ok', result }));
		}).catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (baseRecordMatch && request.method === 'DELETE') {
		void feishuWorkbench.deleteBaseRecord({ recordId: decodeURIComponent(baseRecordMatch[1]) }).then((result) => {
			response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify({ status: 'ok', result }));
		}).catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/feishu-workbench/wiki-documents' && request.method === 'POST') {
		void readJsonBody(request, 64 * 1024).then((payload) => feishuWorkbench.createWikiDocument(payload ?? {})).then((result) => {
			response.writeHead(201, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify({ status: 'created', result }));
		}).catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/feishu-workbench/digests' && request.method === 'POST') {
		void readJsonBody(request, 8 * 1024).then((payload) => feishuWorkbench.publishDigest(payload ?? {})).then((result) => {
			response.writeHead(201, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify({ status: 'created', result }));
		}).catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/feishu-workbench/group-tabs' && request.method === 'POST') {
		void readJsonBody(request, 8 * 1024).then((payload) => feishuWorkbench.ensureWorkbenchTab(payload ?? {})).then((result) => {
			response.writeHead(201, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify({ status: 'created', result }));
		}).catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/feishu-workbench/calendar-events' && request.method === 'POST') {
		void readJsonBody(request, 64 * 1024).then((payload) => feishuWorkbench.createCalendarEvent(payload ?? {})).then((result) => {
			response.writeHead(201, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify({ status: 'created', result }));
		}).catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/feishu-workbench/approvals' && request.method === 'POST') {
		void readJsonBody(request, 128 * 1024).then((payload) => feishuWorkbench.createApproval(payload ?? {})).then((result) => {
			response.writeHead(201, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify({ status: 'created', result }));
		}).catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	if (url.pathname === '/api/feishu-workbench/message-search' && request.method === 'POST') {
		void readJsonBody(request, 16 * 1024).then((payload) => feishuWorkbench.searchMessages(payload ?? {})).then((result) => {
			response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }); response.end(JSON.stringify({ status: 'ok', result }));
		}).catch((error) => response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) })));
		return;
	}
	// API paths must never fall through to the SPA.  A missing API route used to
	// return index.html (200), which surfaced in the dashboard as the misleading
	// "Unexpected token '<'" JSON parse error instead of an actionable 404.
	if (url.pathname.startsWith('/api/') && !(url.pathname === '/api/paper-kb-deliveries' && request.method === 'GET')) {
		response.writeHead(404, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ status: 'not_found', message: `未知 API：${url.pathname}` }));
		return;
	}
	if (frontendMode === 'spa' && request.method === 'GET' && !['/health', '/events', '/metrics', '/jobs', '/analysis/jobs', '/api/paper-kb-deliveries', '/internal/larkagentx/routes'].includes(url.pathname)) {
		const assetPath = resolveFrontendAssetPath(url.pathname, frontendDist, quantFrontendDist);
		if (!assetPath) { response.writeHead(404).end(); return; }
		try { const body = readFileSync(assetPath); const type = assetPath.endsWith('.js') ? 'text/javascript' : assetPath.endsWith('.css') ? 'text/css' : 'text/html; charset=utf-8'; response.writeHead(200, { 'content-type': type, 'cache-control': assetPath.endsWith('index.html') ? 'no-cache' : 'public, max-age=31536000, immutable' }); response.end(body); } catch { response.writeHead(404).end(); }
		return;
	}
if (url.pathname === '/health') {
		response.writeHead(200, { 'content-type': 'application/json' });
		response.end(JSON.stringify({ status: 'ok', events: recentEvents.length,
			quant_alert_configured: Boolean(quantAlertWebhookToken && feishuAlertReceiveId),
			paper_kb_alert_configured: Boolean(paperKbAlertWebhookToken && /^oc_[A-Za-z0-9]+$/.test(paperKbAlertReceiveId)),
			runtime_source: process.env.FEISHU_ADAPTER_SOURCE_MODE === 'source-overlay' ? 'source-overlay' : 'image',
			build: releaseMetadata() }));
		return;
	}
	if (url.pathname === '/internal/quant-alert' && request.method === 'POST') {
		void handleQuantAlert(request, response);
		return;
	}
	if (url.pathname === '/internal/paper-kb-alert' && request.method === 'POST') {
		void handlePaperKbAlert(request, response);
		return;
	}
	if (url.pathname === '/api/paper-kb-deliveries' && request.method === 'GET') {
		const eventId = String(url.searchParams.get('event_id') ?? '').trim().slice(0, 200);
		void ledger.paperKbDeliveryStatus(eventId).then((rows) => {
			response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
			response.end(JSON.stringify({ deliveries: rows, ...(eventId ? { event_id: eventId } : {}) }));
		}).catch((error) => response.writeHead(503).end(JSON.stringify({ status: 'error', message: String(error) })));
		return;
	}
	if (url.pathname === '/internal/feishu-user-oauth' && request.method === 'POST') {
		void handleFeishuUserOauth(request, response);
		return;
	}
	if (url.pathname === '/metrics') {
		void renderMetrics().then((metrics) => { response.writeHead(200, { 'content-type': 'text/plain; version=0.0.4' }); response.end(metrics); }).catch((error) => { response.writeHead(503).end(String(error)); });
		return;
	}
	if (url.pathname === '/jobs' && request.method === 'GET') {
		void ledger.pendingJobs().then((jobs) => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify({ jobs })); }).catch((error) => { response.writeHead(503).end(JSON.stringify({ status: 'error', message: String(error) })); });
		return;
	}
	if (url.pathname === '/analysis/jobs' && request.method === 'GET') {
		void ledger.pendingAnalysis().then((jobs) => { response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }).end(JSON.stringify({ jobs })); }).catch((error) => response.writeHead(503).end(String(error)));
		return;
	}
	if (url.pathname === '/reconcile' && request.method === 'POST') {
		void reconcileNow().then((result) => response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }).end(JSON.stringify(result))).catch((error) => response.writeHead(503, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: String(error) })));
		return;
	}
	if (url.pathname === '/events') {
		response.writeHead(200, {
			'content-type': 'text/event-stream',
			'cache-control': 'no-cache',
			connection: 'keep-alive',
		});
		sendSse(response, 'snapshot', recentEvents);
		eventStreams.add(response);
		request.on('close', () => eventStreams.delete(response));
		return;
	}
	if (url.pathname === '/n8n-status' && request.method === 'POST') {
		const chunks = [];
		request.on('data', (chunk) => chunks.push(chunk));
		request.on('end', async () => {
			try {
				const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
				const event = recentEvents.find((entry) => entry.message_id === payload.message_id);
				if (event) {
					Object.assign(event, {
						n8n_status: payload.n8n_status ?? '已完成',
						target_status: payload.target_status ?? null,
						target_batch_id: payload.target_batch_id ?? null,
						n8n_error: payload.n8n_error ?? null,
					});
					broadcastSnapshot();
				}
				if (payload.n8n_status === '已完成' && payload.message_id) await ledger.queueAnalysisByMessage(payload.message_id, payload.target_batch_id);
				response.writeHead(200, { 'content-type': 'application/json' });
				response.end(JSON.stringify({ status: 'ok' }));
			} catch (error) {
				response.writeHead(400, { 'content-type': 'application/json' });
				response.end(JSON.stringify({ status: 'error', message: String(error) }));
			}
		});
		return;
	}
	if (url.pathname === '/n8n-error' && request.method === 'POST') {
		void (async () => { try { const payload = await readJsonBody(request, 512 * 1024); await ledger.recordError(payload); response.writeHead(202, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'recorded' })); } catch (error) { response.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({ status: 'error', message: String(error) })); } })();
		return;
	}
	if (url.pathname === '/manual-relay' && request.method === 'POST') {
		void handleManualRelay(request, response);
		return;
	}
	if (url.pathname === '/internal/larkagentx/inbound' && request.method === 'POST') {
		void handleLarkAgentXInbound(request, response);
		return;
	}
	if (url.pathname === '/internal/larkagentx/summary' && request.method === 'POST') {
		void handleLarkAgentXSummaryInbound(request, response);
		return;
	}
	if (url.pathname === '/internal/larkagentx/routes' && request.method === 'GET') {
		void handleLarkAgentXRouteCatalog(request, response);
		return;
	}
	if (url.pathname === '/internal/larkagentx/group-relay' && request.method === 'POST') {
		void handleLarkAgentXGroupRelayInbound(request, response);
		return;
	}
	if (url.pathname === '/internal/larkagentx/gap-repair' && request.method === 'POST') {
		void handleLarkAgentXGapRepair(request, response);
		return;
	}
	if (url.pathname === '/internal/larkagentx/retry-failed' && request.method === 'POST') {
		void handleLarkAgentXRetryFailed(request, response);
		return;
	}
	if (url.pathname === '/wechat-group-relay' && request.method === 'POST') {
		void handleWeChatGroupRelay(request, response);
		return;
	}
	if (url.pathname === '/relay-clipboard-draft' && request.method === 'POST') {
		void (async () => {
			try {
				const input = await readJsonBody(request, 1024 * 1024);
				const text = String(input?.text ?? '');
				if (!text.trim()) throw new Error('剪贴板没有可投递的文字');
				response.writeHead(201, { 'content-type': 'application/json' });
				response.end(JSON.stringify({ draft_id: createRelayDraft(text) }));
			} catch (error) {
				response.writeHead(400, { 'content-type': 'application/json' });
				response.end(JSON.stringify({ status: 'error', message: error instanceof Error ? error.message : String(error) }));
			}
		})();
		return;
	}
	if (url.pathname.startsWith('/relay-draft/') && request.method === 'GET') {
		const id = url.pathname.slice('/relay-draft/'.length);
		const draft = relayDrafts.get(id);
		if (!draft || draft.expires_at <= Date.now()) {
			relayDrafts.delete(id);
			response.writeHead(404, { 'content-type': 'application/json' });
			response.end(JSON.stringify({ status: 'error', message: '草稿已过期，请重新运行快捷键' }));
			return;
		}
		relayDrafts.delete(id);
		response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
		response.end(JSON.stringify({ text: draft.text }));
		return;
	}
	if (url.pathname === '/relay') {
		response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
		response.end(relayHtml.replace(RELAY_ROUTE_OPTIONS_SLOT, relayRouteOptions));
		return;
	}
	if (url.pathname === '/') {
		response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
		response.end(dashboardHtml);
		return;
	}
	response.writeHead(404).end();
});

async function persistQueuedResource(resource) {
	if (resource.path) return resource;
	if (!resource.data || !Buffer.isBuffer(resource.data)) throw new Error(`无法持久化待投递媒体：${resource.filename ?? 'unknown'}`);
	const path = join(ingestionStorageDir, `${randomUUID()}-accepted-media.bin`);
	await writeFile(path, resource.data, { flag: 'wx', mode: 0o600 });
	return { ...resource, path };
}

async function forwardToN8n(data, manual = null) {
	// The worker re-enters the proven n8n transport below with a stable job ID.
	// Normal Feishu/manual ingress stops here after durable acceptance, so an
	// unavailable workflow cannot hold the group-history cursor hostage.
	if (manual?.replayJobId) return dispatchToN8n(data, manual);
	const messageText = manual?.messageText ?? String(extractMessagePayload(data.message ?? {}).text ?? '').trim();
	const route = routeFromMessageText(messageText);
	const receivedAt = manual?.receivedAt ?? new Date().toISOString();
	const importContent = manual?.importContent ?? extractImportContent(messageText, { referenceTime: receivedAt });
	const messageId = data?.message?.message_id ?? null;
	const existing = await ledger.getJobByMessageId(messageId);
	if (shouldSkipMessageForward({ existingJob: existing })) return { jobId: existing.job_id, duplicate: true, batchId: existing.remote_batch_id };
	const downloaded = manual?.resources ?? await downloadMedia(data, manual?.messageResourceApi ?? null);
	const resources = [];
	let acceptedJob = null;
	try {
		for (const resource of downloaded) resources.push(await persistQueuedResource(resource));
		const payload = {
			source: manual?.source ?? (manual ? 'manual-relay' : 'feishu'),
			source_label: manual?.sourceLabel ? String(manual.sourceLabel).slice(0, 120) : null,
			receivedAt, event: data, message_text: messageText, import_content: importContent.content,
			...(importContent.content_date ? { content_date: importContent.content_date, content_time: importContent.content_time } : {}),
			text_content_sha256: importContent.content ? createHash('sha256').update(importContent.content).digest('hex') : null,
			resources: resources.map(({ data: _data, path: _path, ...metadata }) => metadata),
			topic_key: route.topic_key ?? sourceRegistry.default_topic_key ?? 'general', publisher_key: route.publisher_key, analyst_id: route.remote_analyst_id,
		};
		const { job, duplicate } = await ledger.getOrCreateJob({
			jobId: randomUUID(), eventId: payload.event?.event_id, messageId: payload.event?.message?.message_id,
			route, payload: { source: payload.source, source_label: payload.source_label, receivedAt, event: payload.event, message_text: messageText, import_content: importContent.content, ...(importContent.content_date ? { content_date: importContent.content_date, content_time: importContent.content_time } : {}), resources: payload.resources }, contentSha256: payload.text_content_sha256,
		});
		if (duplicate) return { jobId: job.job_id, duplicate: true, batchId: job.remote_batch_id };
		acceptedJob = job;
		if (payload.import_content) await ledger.recordContentItem(job.job_id, { content_type: 'text', content_sha256: payload.text_content_sha256, content_date: importContent.content_date, content_time: importContent.content_time, body: importContent.content });
		for (const [ordinal, resource] of resources.entries()) await ledger.recordAsset(job.job_id, ordinal, resource);
		await ledger.updateJob(job.job_id, { status: 'queued', stage: 'delivery_queued' });
		await ledger.enqueueDelivery(job.job_id);
		return { jobId: job.job_id, accepted: true };
	} catch (error) {
		if (acceptedJob) await ledger.updateJob(acceptedJob.job_id, { status: 'failed', stage: 'delivery_accept_failed', error_class: 'local_storage', error_message: error instanceof Error ? error.message : String(error) });
		await Promise.all(resources.map((resource) => resource.path ? unlink(resource.path).catch(() => {}) : Promise.resolve()));
		throw error;
	}
}

async function dispatchToN8n(data, manual = null) {
	const messageText = manual?.messageText ?? String(extractMessagePayload(data.message ?? {}).text ?? '').trim();
	const route = routeFromMessageText(messageText);
	const receivedAt = manual?.receivedAt ?? new Date().toISOString();
	const importContent = manual?.importContent ?? extractImportContent(messageText, { referenceTime: receivedAt });
	const messageId = data?.message?.message_id ?? null;
	const existing = await ledger.getJobByMessageId(messageId);
	if (shouldSkipMessageForward({ existingJob: existing, replayJobId: manual?.replayJobId })) {
		return { jobId: existing.job_id, duplicate: true, batchId: existing.remote_batch_id };
	}
	const resources = manual?.resources ?? await downloadMedia(data, manual?.messageResourceApi ?? null);
	const payload = {
			source: manual?.source ?? (manual ? 'manual-relay' : 'feishu'),
			source_label: manual?.sourceLabel ? String(manual.sourceLabel).slice(0, 120) : null,
			receivedAt,
			event: data,
			message_text: messageText,
			import_content: importContent.content,
		...(importContent.content_date ? { content_date: importContent.content_date, content_time: importContent.content_time } : {}),
			text_content_sha256: importContent.content ? createHash('sha256').update(importContent.content).digest('hex') : null,
		resources: resources.map(({ data: _data, path: _path, ...metadata }) => metadata),
			topic_key: route.topic_key ?? sourceRegistry.default_topic_key ?? 'general',
			publisher_key: route.publisher_key,
		analyst_id: route.remote_analyst_id,
	};
	const { job, duplicate } = await ledger.getOrCreateJob({
		jobId: randomUUID(), eventId: payload.event?.event_id, messageId: payload.event?.message?.message_id,
		route, payload: { source: payload.source, source_label: payload.source_label, receivedAt, event: payload.event, message_text: messageText, import_content: importContent.content, ...(importContent.content_date ? { content_date: importContent.content_date, content_time: importContent.content_time } : {}), resources: payload.resources }, contentSha256: payload.text_content_sha256,
	});
	if (duplicate && !manual?.replayJobId) return { jobId: job.job_id, duplicate: true, batchId: job.remote_batch_id };
	if (payload.import_content) await ledger.recordContentItem(job.job_id, { content_type: 'text', content_sha256: payload.text_content_sha256, content_date: importContent.content_date, content_time: importContent.content_time, body: importContent.content });
	// Do not turn a new Feishu message into a duplicate just because an image or
	// file has identical bytes to a historical attachment.  The upstream media
	// protocol remains idempotent through its per-message batch/item/upload keys.
	await ledger.updateJob(job.job_id, { status: resources.length ? 'uploading' : 'queued', stage: resources.length ? 'uploading_parts' : 'creating_text', attempt_count: Number(job.attempt_count ?? 0) + 1 });
	const assetIds = [];
	const messageItemKey = payload.event?.message?.message_id
		? `i_${String(payload.event.message.message_id).replace(/[^A-Za-z0-9_-]/g, '').slice(0, 52).padEnd(24, '0')}`
		: `i_${randomUUID().replace(/-/g, '')}`;
	const textChunks = splitUtf8Text(importContent.content, { baseKey: messageItemKey });
	for (const [ordinal, resource] of resources.entries()) assetIds.push(await ledger.recordAsset(job.job_id, ordinal, resource));
	const totalBytes = resources.reduce((sum, resource) => sum + resource.declared_bytes, 0);
	if (resources.length) {
		let batchId = manual?.remoteBatchId ?? job.remote_batch_id ?? null;
		for (const resource of resources) {
			let lastUpload = batchId && resource.remote_upload_id ? { batch_id: batchId, upload_id: resource.remote_upload_id } : null;
			let offset = 0;
			for (let partIndex = 0; partIndex < resource.parts.length; partIndex++) {
				const manifest = resource.parts[partIndex];
				if (manifest.uploaded) { offset += manifest.bytes; continue; }
				const bytes = resource.path ? await readAssetPart(resource, offset, manifest.bytes) : resource.data.subarray(offset, offset + manifest.bytes);
				if (bytes.length !== manifest.bytes) throw new Error(`媒体分片大小不一致：${resource.filename} part ${partIndex}`);
				const form = new FormData();
				const fields = {
					analyst_id: payload.analyst_id,
					topic_key: payload.topic_key,
					publisher_key: payload.publisher_key,
					batch_key: payload.event?.message?.message_id ? `b_${String(payload.event.message.message_id).replace(/[^A-Za-z0-9_-]/g, '').slice(0, 52).padEnd(24, '0')}` : `b_${randomUUID().replace(/-/g, '')}`,
					item_key: messageItemKey,
					media_upload_key: `${payload.event?.message?.message_id ? `u_${String(payload.event.message.message_id).replace(/[^A-Za-z0-9_-]/g, '').slice(0, 52).padEnd(24, '0')}` : `u_${randomUUID().replace(/-/g, '')}`}_${resources.indexOf(resource) + 1}`,
					media_filename: resource.filename,
					media_type: resource.media_type,
					media_bytes: String(resource.declared_bytes),
					media_sha256: resource.content_sha256,
					media_last_modified: String(resource.last_modified),
					batch_id: lastUpload?.batch_id ?? batchId ?? '',
					upload_id: lastUpload?.upload_id ?? resource.remote_upload_id ?? '',
					part_index: String(partIndex),
					part_sha256: manifest.sha256,
					// The archive API limits one text item to 1 MiB. Send the complete
					// pre-hashed chunk manifest; n8n writes each chunk into this batch.
					content: textChunks[0]?.content ?? '',
					content_sha256: textChunks[0]?.content_sha256 ?? '',
					content_chunks: JSON.stringify(textChunks),
					content_date: importContent.content_date,
					content_time: importContent.content_time,
					source_label: payload.source_label ?? (payload.source === 'manual-relay' ? '本机手动投递' : '飞书机器人'),
				};
				for (const [key, value] of Object.entries(fields)) form.append(key, String(value ?? ''));
				form.append('media', new Blob([bytes], { type: resource.media_type }), `${resource.filename}.part-${partIndex}`);
				const controller = new AbortController();
				const timer = setTimeout(() => controller.abort(), Math.min(180_000, 30_000 + Math.ceil(totalBytes / uploadPartBytes) * 5_000));
				try {
					const response = await fetchWithBackoff(mediaPartWebhookUrl, { method: 'POST', body: form, signal: controller.signal }, { maxAttempts: 1 });
					if (!response.ok) {
						const remoteText = (await response.text()).slice(0, 500);
						await ledger.updateJob(job.job_id, {
							status: response.status === 409 || response.status >= 500 ? 'retryable_failed' : 'failed',
							stage: 'upload_part', last_http_status: response.status,
							error_class: response.status === 409 ? 'remote_conflict' : 'remote_http',
							error_message: `media part HTTP ${response.status}${remoteText ? `: ${remoteText}` : ''}`,
						});
						throw new Error(`n8n media part webhook returned HTTP ${response.status}: ${remoteText.slice(0, 240)}`);
					}
					try { lastUpload = await response.json(); } catch (error) {
						await ledger.updateJob(job.job_id, { status: 'retryable_failed', stage: 'upload_part', last_http_status: response.status, error_class: 'n8n_protocol', error_message: `n8n 媒体分片响应不是 JSON：${error instanceof Error ? error.message : String(error)}` });
						throw error;
					}
					if (!lastUpload?.batch_id || !lastUpload?.upload_id) {
						await ledger.updateJob(job.job_id, { status: 'retryable_failed', stage: 'upload_part', last_http_status: response.status, error_class: 'n8n_protocol', error_message: `n8n 未返回媒体批次或 upload_id：${JSON.stringify(lastUpload).slice(0, 300)}` });
						throw new Error('n8n 未返回媒体批次或 upload_id');
					}
					batchId = lastUpload.batch_id;
					await ledger.updateJob(job.job_id, { remote_batch_id: batchId, status: 'uploading', stage: 'uploading_parts' });
					await ledger.updateAssetSession(assetIds[resources.indexOf(resource)], 'uploading', lastUpload.upload_id);
					await ledger.recordPart(assetIds[resources.indexOf(resource)], partIndex, response.status);
				} finally { clearTimeout(timer); }
				 offset += bytes.length;
			}
			if (!lastUpload?.batch_id || !lastUpload?.upload_id) throw new Error('缺少可恢复的媒体批次或 upload_id');
			const finalResponse = await fetchWithBackoff(mediaFinalizeWebhookUrl, {
				method: 'POST', headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ batch_id: lastUpload.batch_id, upload_id: lastUpload.upload_id, media_sha256: resource.content_sha256, message_id: payload.event?.message?.message_id ?? null, submit: resources.indexOf(resource) === resources.length - 1 }),
			}, { maxAttempts: 1 });
			if (!finalResponse.ok) throw new Error(`n8n media finalize webhook returned HTTP ${finalResponse.status}: ${(await finalResponse.text()).slice(0, 240)}`);
			await finalResponse.text();
			await ledger.updateJob(job.job_id, { status: 'submitting', stage: 'remote_submit', remote_batch_id: lastUpload.batch_id });
			await ledger.updateAssetSession(assetIds[resources.indexOf(resource)], 'completed', lastUpload.upload_id);
		}
		await ledger.updateJob(job.job_id, { status: 'completed', stage: 'submitted' });
		await Promise.all(resources.map((resource) => resource.path ? unlink(resource.path).catch(() => {}) : Promise.resolve()));
		return { jobId: job.job_id, batchId: job.remote_batch_id };
	}
	const headers = { 'content-type': 'application/json' };
	const body = JSON.stringify(payload);
	const targetWebhookUrl = resources.length ? mediaWebhookUrl : textWebhookUrl;
	const timeoutMs = Math.min(180_000, 30_000 + Math.ceil(totalBytes / uploadPartBytes) * 5_000);
	const controller = new AbortController();
	const timer = setTimeout(() => controller.abort(), timeoutMs);
	try {
		const response = await fetchWithBackoff(targetWebhookUrl, {
			method: 'POST',
			headers,
			body,
			signal: controller.signal,
		}, { maxAttempts: 1 });

		if (!response.ok) {
			const remoteBody = await response.text();
			await ledger.updateJob(job.job_id, { status: response.status >= 500 ? 'retryable_failed' : 'failed', stage: 'text_or_submit', last_http_status: response.status, error_class: 'n8n_webhook', error_message: remoteBody.slice(0, 500) });
			throw new Error(`n8n webhook returned HTTP ${response.status}${remoteBody ? `: ${remoteBody.slice(0, 240)}` : ''}`);
		}

		await response.text();
		await ledger.updateJob(job.job_id, { status: 'completed', stage: 'submitted' });
		return { jobId: job.job_id };
	} finally {
		clearTimeout(timer);
	}
}

async function processSummaryGroupMessage(data, options = {}) {
	const messagePayload = extractMessagePayload(data.message ?? {});
	const messageText = String(messagePayload.text ?? '').trim();
	const tag = messageText.match(/^#([a-z0-9-]+)(?=\s|$)/i)?.[1]?.toLowerCase();
	// All human and bot traffic is observed. Only registered tags are safe to
	// attribute to a remote analyst, so unrelated group chatter is ignored.
	if (!tag || !sourceRoutes.get(tag)?.enabled) return { ignored: true, reason: 'missing_or_unregistered_route_tag' };
	if (isSystemRelayPlaceholder(messageText, tag)) return { ignored: true, reason: 'system_message_filtered' };
	const hasMedia = messagePayload.resources.length > 0;
	if (!hasImportableTaggedPayload(messageText, { hasMedia })) return { ignored: true, reason: 'empty_tagged_payload' };
	const eventId = data.event_id;
	addEvent(data);
	updateEvent(eventId, { n8n_status: hasMedia ? '汇总群媒体转发中' : '汇总群文字转发中' });
	try {
		const result = await forwardToN8n(data, {
			source: options.source ?? 'summary-group-poll', sourceLabel: data.source_label,
			messageResourceApi: options.messageResourceApi === undefined ? feishuUserOauth.sourceApi : options.messageResourceApi,
		});
		updateEvent(eventId, {
			n8n_status: result?.duplicate ? '重复已跳过' : '已接收，处理中',
			target_status: result?.duplicate ? '本地幂等去重，未重复请求远端' : null,
			target_batch_id: result?.batchId ?? null,
		});
		return result;
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		updateEvent(eventId, { n8n_status: '失败', n8n_error: message });
		throw error;
	}
}

function feishuDedupeKeys(data) {
	return [...new Set([
		data?.event_id ? `event:${data.event_id}` : null,
		data?.message?.message_id ? `message:${data.message.message_id}` : null,
	].filter(Boolean))];
}

function isQuantAlertBindingCommand(data) {
	const text = String(extractMessagePayload(data?.message ?? {}).text ?? '').replace(/@_user_\d+\s*/g, '').trim();
	return text === '盘中提醒绑定';
}

// A reading-group message like `收录 2608.21157 2608.19677` asks the KB to pull those
// arXiv papers in.  Only ids are extracted here; the KB revalidates every one of them
// against NNNN.NNNNN before anything reaches a subprocess.
function isPaperIngestCommand(data) {
	const chatId = String(data?.message?.chat_id ?? '');
	if (paperIngestChatId && chatId !== paperIngestChatId && data?.larkagentx_command_lane !== true) return null;
	const text = String(extractMessagePayload(data?.message ?? {}).text ?? '').replace(/@_user_\d+\s*/g, '').trim();
	return parsePaperIngestIds(text);
}

async function forwardPaperIngest(ids, sourceEventId) {
	if (!paperIngestWebhook) throw new Error('PAPER_KB_INGEST_WEBHOOK is not configured');
	return postWebhookWithRetry(paperIngestWebhook, { ids: ids.join(','), source_event_id: sourceEventId }, { timeoutMs: 15_000 });
}

// A reading-group message like `查询 MOE` or `精查 KV cache eviction` asks the KB to
// search its own corpus. `精查`/`深查` opt into the slower LLM full-text rerank; every
// other prefix gets the instant path. Only the query text is extracted here -- it is
// length-capped and never reaches a subprocess on the KB side (see jobs.py:search).
const PAPER_SEARCH_COMMAND = /^(精查|深查|查询|搜索|search)\s*[:：]?\s*(.+)$/i;
const PAPER_SEARCH_DEEP_PREFIX = /^(精查|深查)/;

function isPaperSearchCommand(data) {
	const chatId = String(data?.message?.chat_id ?? '');
	if (paperIngestChatId && chatId !== paperIngestChatId && data?.larkagentx_command_lane !== true) return null;
	const text = String(extractMessagePayload(data?.message ?? {}).text ?? '').replace(/@_user_\d+\s*/g, '').trim();
	const match = text.match(PAPER_SEARCH_COMMAND);
	if (!match) return null;
	const query = match[2].trim();
	if (!query) return null;
	return { query, deep: PAPER_SEARCH_DEEP_PREFIX.test(match[1]) };
}

async function forwardPaperSearch(query, deep, sourceEventId) {
	if (!paperSearchWebhook) throw new Error('PAPER_KB_SEARCH_WEBHOOK is not configured');
	return postWebhookWithRetry(paperSearchWebhook, { query, deep, source_event_id: sourceEventId }, { timeoutMs: 15_000 });
}

function isPaperFeedbackCommand(data) {
	const chatId = String(data?.message?.chat_id ?? '');
	if (paperIngestChatId && chatId !== paperIngestChatId && data?.larkagentx_command_lane !== true) return null;
	const text = String(extractMessagePayload(data?.message ?? {}).text ?? '').replace(/@_user_\d+\s*/g, '').trim();
	return parsePaperFeedback(text);
}

async function forwardPaperFeedback(command, sourceEventId) {
	if (!paperFeedbackWebhook) throw new Error('PAPER_KB_FEEDBACK_WEBHOOK is not configured');
	return postWebhookWithRetry(paperFeedbackWebhook, { ...command, source_event_id: sourceEventId }, { timeoutMs: 15_000 });
}

async function forwardXhsCommand(command, sourceEventId) {
	if (!xhsCommandWebhook) throw new Error('XHS_COMMAND_WEBHOOK is not configured');
	const response = await fetch(xhsCommandWebhook, {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ ...command, event_id: sourceEventId }),
	});
	if (!response.ok) throw new Error(`xhs command webhook responded ${response.status}`);
	return response.status;
}

function pruneFeishuDedupe(now = Date.now()) {
	for (const [key, entry] of feishuEventPromises) {
		if (entry.expiresAt <= now) feishuEventPromises.delete(key);
	}
}

async function processFeishuEvent(data, options = {}) {
	const eventId = data?.event_id ?? 'unknown';
	console.info(`Forwarding im.message.receive_v1 event ${eventId} to n8n`);
	addEvent(data);
	const blockedReason = blockedMessageReason(data?.message);
	if (blockedReason) {
		updateEvent(eventId, { n8n_status: `关键词过滤：${blockedReason}` });
		return { ignored: true, reason: 'blocked_message_keyword', filter_reason: blockedReason };
	}
	// Binding a private alert group is an adapter control command.  It must not
	// be interpreted as analyst research content or require an ingestion route.
	if (isQuantAlertBindingCommand(data)) {
		updateEvent(eventId, { n8n_status: '已识别为盘中提醒绑定命令，未转发研究导入' });
		return { bound_alert_group: true };
	}
	const xhsCommand = parseXhsCommand({
		...data,
		message: { ...(data?.message ?? {}), content: String(extractMessagePayload(data?.message ?? {}).text ?? data?.message?.content ?? '') },
	}, xhsCommandChatId);
	if (xhsCommand) {
		try {
			await forwardXhsCommand(xhsCommand, eventId);
			updateEvent(eventId, { n8n_status: `已转发小红书指令：${xhsCommand.command}` });
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			updateEvent(eventId, { n8n_status: '小红书指令转发失败', n8n_error: message });
			console.error(`小红书指令转发失败：${message}`);
		}
		return { xhs_command: xhsCommand };
	}
	const paperFeedback = isPaperFeedbackCommand(data);
	if (paperFeedback) {
		try {
			await forwardPaperFeedback(paperFeedback, eventId);
			updateEvent(eventId, { n8n_status: `已转发论文推荐反馈：${paperFeedback.action}` });
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			updateEvent(eventId, { n8n_status: '论文推荐反馈转发失败', n8n_error: message });
			console.error(`论文推荐反馈转发失败：${message}`);
		}
		return { paper_feedback: paperFeedback };
	}
	const paperIds = isPaperIngestCommand(data);
	if (paperIds) {
		try {
			await forwardPaperIngest(paperIds, eventId);
			updateEvent(eventId, { n8n_status: `已转发论文收录：${paperIds.join(', ')}` });
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			updateEvent(eventId, { n8n_status: '论文收录转发失败', n8n_error: message });
			console.error(`论文收录转发失败：${message}`);
		}
		return { paper_ingest: paperIds };
	}
	const paperSearch = isPaperSearchCommand(data);
	if (paperSearch) {
		try {
			await forwardPaperSearch(paperSearch.query, paperSearch.deep, eventId);
			updateEvent(eventId, { n8n_status: `已转发论文检索：${paperSearch.query}${paperSearch.deep ? '（精查）' : ''}` });
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			updateEvent(eventId, { n8n_status: '论文检索转发失败', n8n_error: message });
			console.error(`论文检索转发失败：${message}`);
		}
		return { paper_search: paperSearch };
	}
	const hasMedia = extractMessagePayload(data?.message ?? {}).resources.length > 0;
	updateEvent(eventId, { n8n_status: hasMedia ? '下载媒体并转发中' : '转发中' });
	try {
		const result = await forwardToN8n(data, options);
		updateEvent(eventId, { n8n_status: result?.duplicate ? '重复已跳过' : '已接收，处理中', target_status: result?.duplicate ? '本地幂等去重，未重复请求远端' : null });
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		updateEvent(eventId, { n8n_status: '失败', n8n_error: message });
		throw error;
	}
}

// Both the official dispatcher and the private WebSocket are at-least-once
// delivery paths. Keep their short-lived event promise table in one wrapper so
// a retry cannot trigger a second Paper-KB command before the durable job state
// has caught up.
async function dispatchFeishuMessage(data, options = {}) {
	const keys = feishuDedupeKeys(data);
	if (!keys.length || feishuDedupeTtlMs === 0) return processFeishuEvent(data, options);
	const now = Date.now();
	pruneFeishuDedupe(now);
	const existing = keys.map((key) => feishuEventPromises.get(key)).find((entry) => entry && entry.expiresAt > now);
	if (existing) {
		console.info(`Skipping duplicate Feishu event ${data?.event_id ?? data?.message?.message_id ?? 'unknown'}`);
		return existing.promise;
	}

	const promise = processFeishuEvent(data, options);
	const entry = { promise, expiresAt: now + feishuDedupeTtlMs, keys };
	for (const key of keys) feishuEventPromises.set(key, entry);
	try {
		return await promise;
	} catch (error) {
		for (const key of keys) {
			if (feishuEventPromises.get(key) === entry) feishuEventPromises.delete(key);
		}
		throw error;
	}
}

function cardActionInput(data) {
	const value = data?.event?.action?.value ?? data?.action?.value ?? data?.event?.action?.form_value ?? {};
	if (typeof value === 'string') { try { return JSON.parse(value); } catch { return {}; } }
	return value && typeof value === 'object' ? value : {};
}

async function processFeishuCardAction(data) {
	const input = cardActionInput(data);
	const action = String(input.action ?? '');
	const sourceMessageId = String(input.source_message_id ?? '');
	if (!action || !sourceMessageId) return { toast: { type: 'warning', content: '无效的行动卡片参数' } };
	const operator = data?.event?.operator ?? data?.operator ?? {};
	try {
		await feishuWorkbench.performAction({
			sourceMessageId, action,
			operatorOpenId: String(operator?.open_id ?? operator?.operator_id?.open_id ?? ''),
			operatorName: String(operator?.name ?? ''),
		});
		return { toast: { type: 'success', content: '已更新协作状态' } };
	} catch (error) {
		console.error(`飞书行动卡片处理失败：${error instanceof Error ? error.message : String(error)}`);
		return { toast: { type: 'error', content: error instanceof Error ? error.message.slice(0, 80) : '操作失败' } };
	}
}

async function processFeishuReaction(data) {
	const event = data?.event ?? data ?? {};
	const messageId = String(event?.message_id ?? event?.reaction?.message_id ?? '');
	const emoji = String(event?.reaction?.reaction_type?.emoji_type ?? event?.reaction_type?.emoji_type ?? '').toUpperCase();
	const action = ({ THUMBSUP: 'research', EYES: 'focus', CHECK_MARK: 'task', CROSS_MARK: 'ignore' })[emoji];
	if (!messageId || !action) return;
	const record = await ledger.getRelayMessageByActionCard(messageId);
	if (!record) return;
	const operator = event?.operator ?? event?.user ?? {};
	await feishuWorkbench.performAction({ sourceMessageId: record.source_message_id, action, operatorOpenId: String(operator?.open_id ?? operator?.operator_id?.open_id ?? ''), operatorName: String(operator?.name ?? '') });
}

async function processBotMenuEvent(data) {
	const event = data?.event ?? data ?? {};
	const eventKey = String(event?.event_key ?? event?.key ?? '');
	if (eventKey !== 'workbench') return;
	const h5Base = String(process.env.FEISHU_WORKBENCH_PUBLIC_BASE_URL ?? '').replace(/\/$/, '');
	const openId = event?.operator?.open_id ?? event?.operator_id?.open_id;
	if (!h5Base || !openId) return;
	const result = await larkClient.im.v1.message.create({ params: { receive_id_type: 'open_id' }, data: {
		receive_id: openId, msg_type: 'interactive', content: JSON.stringify({
			config: { wide_screen_mode: true }, header: { title: { tag: 'plain_text', content: '分析师工作台' }, template: 'blue' },
			elements: [{ tag: 'div', text: { tag: 'lark_md', content: '打开工作台查看群监听健康、协作行动与已配置能力。' } }, { tag: 'action', actions: [{ tag: 'button', type: 'primary', text: { tag: 'plain_text', content: '打开工作台' }, url: `${h5Base}/workbench` }] }],
		}),
	} });
	if (result.code && result.code !== 0) throw new Error(`发送工作台菜单回复失败：${result.msg ?? result.code}`);
}

const eventDispatcher = new Lark.EventDispatcher({ loggerLevel: Lark.LoggerLevel.info }).register({
	'im.message.receive_v1': async (data) => {
		return dispatchFeishuMessage(data);
	},
	'card.action.trigger': async (data) => { noteWorkbenchEvent('card.action.trigger'); return processFeishuCardAction(data); },
	'im.message.reaction.created_v1': async (data) => { noteWorkbenchEvent('im.message.reaction.created_v1'); return processFeishuReaction(data); },
	'application.bot.menu_v6': async (data) => { noteWorkbenchEvent('application.bot.menu_v6'); return processBotMenuEvent(data); },
});

const wsClient = new Lark.WSClient({
	appId,
	appSecret,
	domain: Lark.Domain.Feishu,
	loggerLevel: Lark.LoggerLevel.info,
});

process.on('unhandledRejection', (error) => {
	console.error('Unhandled adapter rejection', error);
});

dashboard.listen(dashboardPort, dashboardHost, () => {
	console.info(`Feishu monitor available on port ${dashboardPort}`);
});
if (longConnectionEnabled) {
	console.info('Starting Feishu long-connection client');
	wsClient.start({ eventDispatcher });
} else {
	console.info('Feishu long-connection client is disabled');
}
