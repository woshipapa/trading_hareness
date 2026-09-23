import { existsSync } from 'node:fs';
import { join } from 'node:path';

const FEISHU_ROUTES = ['/monitor', '/dashboard', '/workbench', '/relay'];

export function resolveFrontendAssetPath(pathname, feishuDist, quantDist, exists = existsSync) {
	const feishuRoute = FEISHU_ROUTES.some((route) => pathname === route || pathname.startsWith(`${route}/`));
	const requested = pathname.slice(1);
	const relativeAsset = requested.includes('.') ? requested : 'index.html';
	const candidates = requested.startsWith('assets/')
		? [join(quantDist, relativeAsset), join(feishuDist, relativeAsset)]
		: [join(feishuRoute ? feishuDist : quantDist, relativeAsset)];
	return candidates.find((candidate) => exists(candidate)) ?? null;
}
