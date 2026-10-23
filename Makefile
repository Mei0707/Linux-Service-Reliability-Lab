-include .env

PYTHON ?= .venv/bin/python
ARGS ?=
HTTP_PORT ?= 8088
PROMETHEUS_PORT ?= 9095
REDIS_PORT ?= 56379
CACHE_ENABLED ?= true
CACHE_TTL ?= 60
LAB_URL ?= http://127.0.0.1:$(HTTP_PORT)

.PHONY: setup up down test integration lint verify benchmark benchmark-all smoke drills-verify seed
setup:
	python3 -m venv .venv
	$(PYTHON) -m pip install -r requirements-dev.txt

up:
	docker compose up -d --build --wait

down:
	docker compose down

seed:
	$(PYTHON) -m scripts.seed

test:
	$(PYTHON) -m pytest -q -m 'not integration'

integration:
	LAB_INTEGRATION=1 LAB_URL="$(LAB_URL)" REDIS_PORT="$(REDIS_PORT)" CACHE_ENABLED="$(CACHE_ENABLED)" CACHE_TTL="$(CACHE_TTL)" $(PYTHON) -m pytest -q

lint:
	.venv/bin/ruff check app load scripts tests infra/gunicorn.conf.py
	.venv/bin/ruff format --check app load scripts tests infra/gunicorn.conf.py
	docker compose config --quiet

verify: lint integration
	docker compose exec -T nginx nginx -t
	docker compose exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
	docker compose exec -T prometheus promtool test rules /etc/prometheus/alerts.test.yml

benchmark:
	$(PYTHON) -m scripts.benchmark --target "$(LAB_URL)" --prometheus "http://127.0.0.1:$(PROMETHEUS_PORT)" $(ARGS)

benchmark-all:
	$(PYTHON) -m scripts.benchmark --experiments all --target "$(LAB_URL)" --prometheus "http://127.0.0.1:$(PROMETHEUS_PORT)" $(ARGS)

smoke:
	$(PYTHON) -m scripts.benchmark --users 20 --warmup 3 --duration 10 --repeats 1 --max-error-rate 0 --target "$(LAB_URL)" --prometheus "http://127.0.0.1:$(PROMETHEUS_PORT)" $(ARGS)

drills-verify:
	LAB_URL="$(LAB_URL)" $(PYTHON) -m scripts.validate_drills
