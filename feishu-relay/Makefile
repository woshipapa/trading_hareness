.PHONY: adapter-test bridge-test dashboard standalone-up standalone-down

adapter-test:
	cd adapter && npm ci --no-audit --no-fund && npm test

bridge-test:
	PYTHONPATH=bridge python3 -m unittest discover -s bridge -p 'test_*.py' -q

dashboard:
	cd dashboard && npm ci && npm run typecheck && npm test -- --run && npm run build

standalone-up:
	docker compose -f compose.standalone.yaml up --build

standalone-down:
	docker compose -f compose.standalone.yaml down --volumes --remove-orphans
