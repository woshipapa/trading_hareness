// The dashboard's research routes: dashboard path -> quant-research path.
//
// quant-research's OpenAPI document is the route contract, and
// frontend/src/api/generated.ts is its checked-in copy (CI regenerates it from
// the running service and fails if it is stale). scripts/test_relay_research_routes.py
// checks every upstream here against that copy, and every research path the
// dashboards call against this table, so a renamed route fails CI instead of
// turning into a 404 on the edge.
//
// Template paths: `{name}` matches one segment shaped as PARAMS says and is
// copied into the same place in the upstream, which must be the OpenAPI path
// key itself.
import { personalDecisionResearchPaths } from './personal-decision-routes.mjs';

// GET dashboard path -> GET quant path
export const researchReads = new Map([
	['/api/research/runtime/health', '/health'],
	['/api/research/overview', '/api/v1/research/overview'],
	['/api/research/reports', '/api/v1/remote-archive/reports'],
	['/api/research/remote-archive/messages', '/api/v1/remote-archive/messages'],
	['/api/research/claims', '/api/v1/analyst-claims'],
	['/api/research/providers', '/api/v1/providers/health'],
	['/api/research/providers/realtime-health', '/api/v1/providers/realtime-health'],
	// Keep the generated OpenAPI path readable through the edge as well as the dashboard alias.
	['/api/v1/providers/realtime-health', '/api/v1/providers/realtime-health'],
	['/api/research/provider-capabilities', '/api/v1/providers/capabilities'],
	['/api/research/datasources/catalog', '/api/v1/datasources/catalog'],
	['/api/research/quality', '/api/v1/data-quality/issues'],
	['/api/research/recommendations', '/api/v1/recommendations/latest'],
	['/api/research/universes/core', '/api/v1/universes/core'],
	['/api/research/features/latest', '/api/v1/features/latest'],
	['/api/research/claim-review', '/api/v1/claim-review'],
	['/api/research/factors', '/api/v1/factors'],
	['/api/research/factor-evaluations', '/api/v1/factors/evaluations'],
	['/api/research/strategies', '/api/v1/strategies'],
	['/api/research/strategy-experiments', '/api/v1/strategies/experiments'],
	['/api/research/strategy-experiments-watchlist', '/api/v1/strategies/experiments'],
	['/api/research/frameworks', '/api/v1/research-frameworks'],
	['/api/research/training/roadmap', '/api/v1/training/roadmap'],
	['/api/research/data-readiness/history-estimate', '/api/v1/data-readiness/history-estimate'],
	['/api/research/data-readiness/features', '/api/v1/data-readiness/features'],
	['/api/research/data-readiness/replay', '/api/v1/data-readiness/replay'],
	['/api/research/tushare/raw', '/api/v1/providers/tushare/raw'],
	['/api/research/minute/imports', '/api/v1/market/minute/imports'],
	['/api/research/market/snapshots', '/api/v1/market/snapshots'],
	['/api/research/market/sectors', '/api/v1/market/sectors'],
	['/api/research/market/sector-flows', '/api/v1/market/sectors/flows'],
	['/api/research/market/sectors/concepts', '/api/v1/market/sectors/concepts'],
	['/api/research/market/sectors/concepts/candidates', '/api/v1/market/sectors/concepts/candidates'],
	['/api/research/market/sectors/concepts/members/backfill/status', '/api/v1/market/sectors/concepts/members/backfill/status'],
	['/api/research/market/sectors/review/report/latest', '/api/v1/market/sectors/review/report/latest'],
	['/api/research/market/sectors/intraday/curves', '/api/v1/market/sectors/intraday/curves'],
	['/api/research/market/flow/features', '/api/v1/market/flow/features'],
	['/api/research/intraday/board-rotations/latest', '/api/v1/intraday/board-rotations/latest'],
	['/api/research/intraday/board-stock-mining/latest', '/api/v1/intraday/board-stock-mining/latest'],
	['/api/research/intraday/limit-linkage/latest', '/api/v1/intraday/limit-linkage/latest'],
	['/api/research/market/radar', '/api/v1/market/radar'],
	['/api/research/market/limit-detail', '/api/v1/market/limit-detail'],
	['/api/research/market/temperature/daily', '/api/v1/market/temperature/daily'],
	['/api/research/market/temperature/intraday', '/api/v1/market/temperature/intraday'],
	['/api/research/strategy/cards', '/api/v1/strategies/cards'],
	['/api/research/indicators', '/api/v1/indicators'],
	['/api/research/indicators/health', '/api/v1/indicators/health'],
	['/api/research/datasources/board', '/api/v1/datasources/board'],
	['/api/research/strategy/board', '/api/v1/strategies/board'],
	['/api/research/strategy/reviews/latest', '/api/v1/strategy/reviews/latest'],
	['/api/research/strategy/post-close/latest', '/api/v1/strategy/post-close/latest'],
	['/api/research/strategy/ablation/latest', '/api/v1/strategy/ablation/latest'],
	['/api/research/strategy/health', '/api/v1/strategy/health'],
	['/api/research/strategy/pattern-mining/latest', '/api/v1/strategy/pattern-mining/latest'],
	['/api/research/ten-day-leader-rotation/latest', '/api/v1/research/ten-day-leader-rotation/latest'],
	['/api/research/intraday/outcomes/latest', '/api/v1/intraday/outcomes/latest'],
	['/api/research/paper/status', '/api/v1/paper/status'],
	...personalDecisionResearchPaths,
	['/api/research/strategy/contracts', '/api/v1/strategy/contracts'],
	['/api/research/strategy/funnel', '/api/v1/strategy/funnel'],
	['/api/research/intraday/services/status', '/api/v1/intraday/services/status'],
	['/api/research/intraday/watchlists', '/api/v1/intraday/watchlists'],
	['/api/research/intraday/scans/latest', '/api/v1/intraday/scans/latest'],
	['/api/research/strategy/decisions/latest', '/api/v1/strategy/decisions/latest'],
	['/api/research/strategy/promotion', '/api/v1/strategy/promotion'],
	['/api/research/strategy/watchlist-proposals', '/api/v1/strategy/watchlist-proposals'],
	['/api/research/analyst-scorecards', '/api/v1/analyst-scorecards'],
	['/api/research/analyst-research/observations', '/api/v1/analyst-research/observations'],
	['/api/research/analyst-research/status', '/api/v1/analyst-research/status'],
	['/api/research/analyst-skills', '/api/v1/analyst-skills'],
	['/api/research/analyst-research/sync-health', '/api/v1/analyst-research/sync-health'],
	['/api/research/analyst-research/market-evaluation', '/api/v1/analyst-research/market-evaluation'],
	['/api/research/analyst-research/stock-timeline', '/api/v1/analyst-research/stock-timeline'],
	['/api/research/analyst-research/reviews', '/api/v1/analyst-research/reviews'],
	['/api/research/analyst-research/reviews/latest', '/api/v1/analyst-research/reviews/latest'],
	['/api/research/research-runs', '/api/v1/research/runs'],
	['/api/research/strategy/daily-summary/latest', '/api/v1/strategy/daily-summary/latest'],
	['/api/research/agent/context', '/api/v1/agent/context'],
	['/api/research/automation/runs', '/api/v1/automation/runs'],
	['/api/research/analyst-prompt-lab/status', '/api/v1/analyst-prompt-lab/status'],
	['/api/research/strategy/governance', '/api/v1/strategy/governance'],
	['/api/research/events/announcements', '/api/v1/events/announcements'],
	['/api/research/events/lhb', '/api/v1/events/lhb'],
]);


// POST dashboard path -> POST quant path
export const researchActions = new Map([
	['/api/research/pipeline/daily', '/api/v1/pipeline/daily'],
	['/api/research/snapshots/build', '/api/v1/data-snapshots/build'],
	['/api/research/reports/reprocess', '/api/v1/remote-archive/reports/reprocess'],
	['/api/research/outcomes/recompute', '/api/v1/outcomes/recompute'],
	['/api/research/intraday/outcomes/recompute', '/api/v1/intraday/outcomes/recompute'],
	['/api/research/scorecards/recompute', '/api/v1/analyst-scorecards/recompute'],
	['/api/research/features/build', '/api/v1/features/build'],
	['/api/research/recommendations/generate', '/api/v1/recommendations/generate'],
	['/api/research/universes/members', '/api/v1/universes/members'],
	['/api/research/factors/evaluate', '/api/v1/factors/evaluate'],
	['/api/research/strategies/backtest', '/api/v1/strategies/backtest'],
	['/api/research/strategy/post-close/run', '/api/v1/strategy/post-close/run'],
	['/api/research/strategy/pattern-mining/run', '/api/v1/strategy/pattern-mining/run'],
	['/api/research/ten-day-leader-rotation/run', '/api/v1/research/ten-day-leader-rotation/run'],
	['/api/research/strategy/watchlist-main-wave/run', '/api/v1/strategy/watchlist-main-wave/run'],
	['/api/research/market/universe/sync', '/api/v1/market/universe/sync'],
	['/api/research/market/full-daily/sync', '/api/v1/market/sync/full-daily'],
	['/api/research/market/full-daily-controls/sync', '/api/v1/market/sync/full-daily-controls'],
	['/api/research/market/post-close/refresh', '/api/v1/market/post-close/refresh'],
	['/api/research/market/flow/features/rebuild', '/api/v1/market/flow/features/rebuild'],
	['/api/research/market/snapshots/run', '/api/v1/market/snapshots/run'],
	['/api/research/market/sectors/sync', '/api/v1/market/sectors/sync'],
	['/api/research/market/sector-flows/sync', '/api/v1/market/sectors/flows/sync'],
	['/api/research/market/sectors/concepts/sync', '/api/v1/market/sectors/concepts/sync'],
	['/api/research/market/sectors/review/report/run', '/api/v1/market/sectors/review/report/run'],
	['/api/research/market/sectors/concepts/members/backfill/run', '/api/v1/market/sectors/concepts/members/backfill/run'],
	['/api/research/market/sectors/concepts/candidates/sync', '/api/v1/market/sectors/concepts/candidates/sync'],
	['/api/research/market/sectors/concepts/research/run', '/api/v1/market/sectors/concepts/research/run'],
	['/api/research/events/cninfo/sync', '/api/v1/events/cninfo/sync'],
	['/api/research/providers/akshare/probe', '/api/v1/providers/akshare/probe'],
	['/api/research/operations/fetch-runs/reconcile-stale', '/api/v1/operations/fetch-runs/reconcile-stale'],
	['/api/research/analyst-prompt-lab/materialize', '/api/v1/analyst-prompt-lab/materialize'],
	['/api/research/analyst-intraday-outcomes/recompute', '/api/v1/analyst-intraday-outcomes/recompute'],
	['/api/research/analyst-research/reviews/run', '/api/v1/analyst-research/reviews/run'],
]);

// Routes with a path parameter, or with a method of their own.
export const researchTemplateRoutes = [
	{ method: 'GET', path: '/api/research/research-runs/{research_run_id}', upstream: '/api/v1/research/runs/{research_run_id}' },
	{ method: 'GET', path: '/api/research/datasources/capabilities/{capability}', upstream: '/api/v1/datasources/capabilities/{capability}' },
	{ method: 'GET', path: '/api/research/datasources/read/{source}/{capability}', upstream: '/api/v1/datasources/read/{source}/{capability}' },
	{ method: 'GET', path: '/api/research/intraday/decision-cards/{symbol}', upstream: '/api/v1/intraday/decision-cards/{symbol}' },
	{ method: 'PUT', path: '/api/research/paper/accounts', upstream: '/api/v1/paper/accounts' },
	{ method: 'POST', path: '/api/research/paper/decisions/{decision_id}/accept', upstream: '/api/v1/paper/decisions/{decision_id}/accept' },
	{ method: 'POST', path: '/api/research/analyst-prompt-lab/candidates/{candidate_id}/label', upstream: '/api/v1/analyst-prompt-lab/candidates/{candidate_id}/label' },
	{ method: 'POST', path: '/api/research/analyst-prompt-lab/evaluate/{variant_key}', upstream: '/api/v1/analyst-prompt-lab/evaluate/{variant_key}' },
	{ method: 'POST', path: '/api/research/stocks/{symbol}/study', upstream: '/api/v1/stocks/{symbol}/study' },
	{ method: 'POST', path: '/api/research/claim-review/{review_id}', upstream: '/api/v1/claim-review/{review_id}' },
];

const UUID = '[0-9a-f-]{36}';
const PARAMS = {
	research_run_id: { pattern: UUID },
	decision_id: { pattern: UUID },
	candidate_id: { pattern: UUID },
	review_id: { pattern: UUID },
	variant_key: { pattern: 'strict_action|scenario_context|risk_first' },
	capability: { pattern: '[a-z][a-z0-9_]*(?:\\.[a-z0-9_]+)+', lowercase: true },
	source: { pattern: '[a-z][a-z0-9_]*', lowercase: true },
	symbol: { pattern: String.raw`\d{6}\.(?:SH|SZ|BJ)`, upper: true },
};

function compile(route) {
	const names = [];
	const source = route.path.split('/').map((segment) => {
		const name = /^\{([a-z_]+)\}$/.exec(segment)?.[1];
		if (!name) return segment.replace(/[.*+?^$()|[\]\\]/g, '\\$&');
		if (!PARAMS[name]) throw new Error(`research route ${route.path}: no PARAMS entry for {${name}}`);
		names.push(name);
		return `(${PARAMS[name].pattern})`;
	}).join('/');
	return { ...route, names, regex: new RegExp(`^${source}$`, 'i') };
}

const compiledTemplates = researchTemplateRoutes.map(compile);

/** The quant request for a dashboard request, or null when it is not a research route. */
export function matchResearchRoute(method, pathname) {
	if (method === 'GET' && researchReads.has(pathname)) return { kind: 'read', method, upstream: researchReads.get(pathname) };
	if (method === 'POST' && researchActions.has(pathname)) return { kind: 'action', method, upstream: researchActions.get(pathname) };
	for (const route of compiledTemplates) {
		if (route.method !== method) continue;
		const match = route.regex.exec(pathname);
		if (!match) continue;
		if (route.names.some((name, index) => PARAMS[name].lowercase && match[index + 1] !== match[index + 1].toLowerCase())) continue;
		let upstream = route.upstream;
		route.names.forEach((name, index) => {
			const value = PARAMS[name].upper ? match[index + 1].toUpperCase() : match[index + 1];
			upstream = upstream.replace(`{${name}}`, encodeURIComponent(value));
		});
		return { kind: method === 'GET' ? 'read' : 'action', method, upstream };
	}
	return null;
}

/** Every route as {method, path, upstream} templates, for the contract test. */
export function researchRouteTable() {
	return [
		...[...researchReads].map(([path, upstream]) => ({ method: 'GET', path, upstream })),
		...[...researchActions].map(([path, upstream]) => ({ method: 'POST', path, upstream })),
		...researchTemplateRoutes.map(({ method, path, upstream }) => ({ method, path, upstream })),
	];
}
