// Source tags that feed downstream research data.
//
// The registry file declares the tags that ship with a release.  A tag first
// used from the dashboard is registered here while the adapter runs and kept in
// ingestion_source_profiles, so a new source group needs no release.  A tag the
// file declares always follows the file.

const TAG_PATTERN = /^[a-z0-9][a-z0-9_-]{0,31}$/;

function normalizeTag(value) {
	const tag = String(value ?? '').trim().replace(/^#/, '').toLowerCase();
	if (!TAG_PATTERN.test(tag)) throw new Error('标签必须为 1–32 位小写字母、数字、- 或 _，且不能以符号开头');
	return tag;
}

function routeFromProfile(row) {
	return {
		tag: String(row.source_tag).toLowerCase(), label: row.label, topic_key: row.topic_key, publisher_key: row.publisher_key,
		remote_analyst_id: row.analyst_id, enabled: row.enabled !== false, origin: 'dashboard',
	};
}

export function createSourceTags({ registry, ledger }) {
	const defaultTopicKey = registry.default_topic_key ?? 'general';
	// The map every reader of the registry uses; entries added here are seen by them at once.
	const routes = new Map((registry.routes ?? []).map((route) => [String(route.tag).toLowerCase(), route]));
	const fileTags = new Set(routes.keys());

	function describe(value) {
		const tag = String(value ?? '').trim().replace(/^#/, '').toLowerCase();
		const route = routes.get(tag);
		if (!route) return { tag, registered: false, enabled: false, origin: null, label: null };
		return { tag, registered: true, enabled: route.enabled !== false, origin: fileTags.has(tag) ? 'file' : 'dashboard', label: route.label ?? tag };
	}

	// Tags registered from the dashboard in an earlier run.  A stored profile
	// without that origin came from a registry file that no longer declares it,
	// and stays unregistered.
	async function load() {
		for (const row of await ledger.sourceProfiles()) {
			const tag = String(row.source_tag).toLowerCase();
			if (fileTags.has(tag) || row.config?.origin !== 'dashboard') continue;
			routes.set(tag, routeFromProfile(row));
		}
		return routes;
	}

	// A tag that is already registered is left as it is, except that a dashboard
	// tag switched off earlier is switched on again.
	async function register({ tag, label = '' }) {
		const key = normalizeTag(tag);
		const current = routes.get(key);
		if (current && (fileTags.has(key) || current.enabled !== false)) return { ...describe(key), created: false };
		const route = current
			? { ...current, enabled: true }
			: { tag: key, label: String(label ?? '').trim().slice(0, 60) || key, topic_key: defaultTopicKey, publisher_key: key, remote_analyst_id: key, enabled: true, origin: 'dashboard' };
		await ledger.saveSourceProfile(route, defaultTopicKey);
		routes.set(key, route);
		return { ...describe(key), created: !current };
	}

	// Only a tag registered from the dashboard can be switched here.
	async function setEnabled(tag, enabled) {
		const key = normalizeTag(tag);
		const current = routes.get(key);
		if (!current || (current.enabled !== false) === Boolean(enabled)) return describe(key);
		if (fileTags.has(key)) throw new Error(`标签 #${key} 由仓库登记表声明，不能在看板里${enabled ? '开启' : '关闭'}`);
		const route = { ...current, enabled: Boolean(enabled) };
		await ledger.saveSourceProfile(route, defaultTopicKey);
		routes.set(key, route);
		return describe(key);
	}

	function list() {
		return [...routes.keys()].sort().map(describe);
	}

	return { routes, load, register, setEnabled, describe, list };
}
