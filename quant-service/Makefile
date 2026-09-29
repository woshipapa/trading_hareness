.PHONY: test standalone-up standalone-down dashboard

test:
	python -m unittest discover -s tests -q

standalone-up:
	docker compose -f compose.standalone.yaml up --build

standalone-down:
	docker compose -f compose.standalone.yaml down --volumes --remove-orphans

dashboard:
	cd ../frontend && npm ci && npm run typecheck && npm run build
