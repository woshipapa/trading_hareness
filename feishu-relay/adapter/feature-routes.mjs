function routeMatch(route, pathname) {
	if (typeof route.path === 'string') {
		const parts = route.path.split('/');
		const actual = pathname.split('/');
		if (parts.length !== actual.length) return null;
		const params = {};
		for (let index = 0; index < parts.length; index += 1) {
			if (parts[index].startsWith(':')) {
				try { params[parts[index].slice(1)] = decodeURIComponent(actual[index]); } catch { return null; }
			}
			else if (parts[index] !== actual[index]) return null;
		}
		return params;
	}
	const expression = route.path.global || route.path.sticky ? new RegExp(route.path.source, route.path.flags.replace(/[gy]/g, '')) : route.path;
	const match = expression.exec(pathname);
	if (!match) return null;
	try {
		const params = Object.fromEntries(Object.entries(match.groups ?? {}).map(([key, value]) => [key, decodeURIComponent(value)]));
		return { ...params, ...match.slice(1).reduce((values, value, index) => ({ ...values, [index]: decodeURIComponent(value) }), {}) };
	} catch { return null; }
}

function writeJson(response, status, payload) {
	response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
	response.end(JSON.stringify(payload));
}

export function matchesFeatureRoute(routes, method, pathname) {
	return Boolean(findFeatureRoute(routes, method, pathname));
}

function findFeatureRoute(routes, method, pathname) {
	const routeList = routes.flatMap((module) => module.routes ?? module);
	return routeList.filter((route) => String(route.method).toUpperCase() === String(method).toUpperCase())
		.map((route) => ({ route, params: routeMatch(route, pathname) }))
		.find(({ params }) => params !== null) ?? null;
}

export function createFeatureRoutes({ routes, readJsonBody, token }) {
	const routeList = routes.flatMap((module) => module.routes ?? module);
	const dispatch = async function dispatch(request, response, match = null) {
		const url = new URL(request.url ?? '/', 'http://localhost');
		const found = match ?? findFeatureRoute(routeList, request.method, url.pathname);
		if (!found) return false;
		const { route, params } = found;
		if (route.internal && (!token || request.headers?.['x-larkagentx-token'] !== token)) {
			writeJson(response, 403, { status: 'forbidden' });
			return true;
		}
		try {
			const body = request.method === 'GET' || request.method === 'HEAD' ? undefined : await readJsonBody(request);
			const result = await route.handle({ params, query: url.searchParams, body });
			writeJson(response, 200, result ?? {});
		} catch (error) {
			writeJson(response, 400, { status: 'error', message: error instanceof Error ? error.message : String(error) });
		}
		return true;
	};
	dispatch.matches = (method, pathname) => matchesFeatureRoute(routeList, method, pathname);
	dispatch.lookup = (method, pathname) => findFeatureRoute(routeList, method, pathname);
	return dispatch;
}
