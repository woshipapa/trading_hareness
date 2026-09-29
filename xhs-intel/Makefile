.PHONY: test standalone-up standalone-down

test:
	python3 -m unittest discover -s . -p 'test_*.py' -q

standalone-up:
	docker compose -f compose.standalone.yaml up --build

standalone-down:
	docker compose -f compose.standalone.yaml down --volumes --remove-orphans
