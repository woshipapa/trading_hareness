function routeMatch(route, pathname) {
	if (typeof route.path === 'string') {
		const parts = route.path.split('/');
		const actual = pathname.split('/');
		if (parts.length !== actual.length) return null;
		const params = {};
		for (let index = 0; index < parts.length; index += 1) {
			if (parts[index].startsWith(':')) params[parts[index].slice(1)] = decodeURIComponent(actual[index]);
			else if (parts[index] !== actual[index]) return null;
		}
		return params;
	}
	const expression = route.path.global || route.path.sticky ? new RegExp(route.path.source, route.path.flags.replace(/[gy]/g, '')) : route.path;
	const match = expression.exec(pathname);
	if (!match) return null;
	return { ...(match.groups ?? {}), ...match.slice(1).reduce((params, value, index) => ({ ...params, [index]: value }), {}) };
}

function writeJson(response, status, payload) {
	response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
	response.end(JSON.stringify(payload));
}

export function createFeatureRoutes({ routes, readJsonBody, token }) {
	const routeList = routes.flatMap((module) => module.routes ?? module);
	return async function dispatch(request, response) {
		const url = new URL(request.url ?? '/', 'http://localhost');
		const route = routeList.find((candidate) => String(candidate.method).toUpperCase() === String(request.method).toUpperCase() && routeMatch(candidate, url.pathname));
		if (!route) return false;
		if (route.internal && request.headers?.['x-larkagentx-token'] !== token) {
			writeJson(response, 403, { status: 'forbidden' });
			return true;
		}
		try {
			const params = routeMatch(route, url.pathname);
			const body = request.method === 'GET' || request.method === 'HEAD' ? undefined : await readJsonBody(request);
			const result = await route.handle({ params, query: url.searchParams, body });
			writeJson(response, 200, result ?? {});
		} catch (error) {
			writeJson(response, 400, { status: 'error', message: error instanceof Error ? error.message : String(error) });
		}
		return true;
	};
}
