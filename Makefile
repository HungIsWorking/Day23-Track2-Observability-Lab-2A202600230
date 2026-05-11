## Day 23 Track 2 — Observability Lab orchestration
##
## Quick start:
##   make setup    # one-time: pull images, create .env
##   make up       # start the 7-service stack
##   make smoke    # verify all services healthy
##   make demo     # run end-to-end demo (load + alert + trace + drift)
##   make verify   # rubric gate — exit 0 if all checkpoints pass
##   make down     # stop the stack
##   make clean    # stop + remove volumes (destructive)

SHELL := /bin/bash
COMPOSE ?= docker compose

.PHONY: help setup up down restart logs smoke load alert trace drift demo verify clean lint-dashboards

help:
	@grep -E '^##|^[a-zA-Z_-]+:.*?## ' Makefile | sed -E 's/^## ?//; s/:.*## /\t/' | column -t -s $$'\t'

setup: ## one-time install + .env scaffold
	@test -f .env || cp .env.example .env
	@bash 00-setup/pull-images.sh
	@python3 00-setup/verify-docker.py

up: ## start the stack
	$(COMPOSE) up -d
	@echo "Stack starting. Run 'make smoke' to verify (allow ~30s for first start)."

down: ## stop the stack (preserves volumes)
	$(COMPOSE) down

restart: down up ## stop + start

logs: ## tail logs from all services
	$(COMPOSE) logs -f --tail=50

smoke: ## health-check all 7 services
	@echo "Checking services..."
	@curl -fsS http://localhost:8000/healthz   > /dev/null && echo "  app:           OK"
	@curl -fsS http://localhost:9090/-/healthy > /dev/null && echo "  prometheus:    OK"
	@curl -fsS http://localhost:9093/-/healthy > /dev/null && echo "  alertmanager:  OK"
	@curl -fsS http://localhost:3000/api/health | grep -Eq '"database"[[:space:]]*:[[:space:]]*"ok"' && echo "  grafana:       OK"
	@curl -fsS http://localhost:3100/ready     > /dev/null && echo "  loki:          OK"
	@curl -fsS http://localhost:16686/         > /dev/null && echo "  jaeger:        OK"
	@curl -fsS http://localhost:8888/metrics   > /dev/null && echo "  otel-collector: OK"
	@echo "Stack healthy."

load: ## run baseline locust load (concurrency=10, 60s)
	cd 02-prometheus-grafana/load-test && \
	  locust -f locustfile.py --headless -u 10 -r 2 -t 60s --host http://localhost:8000

alert: ## trigger an alert by killing the app, wait, then restore
	bash scripts/trigger-alert.sh

trace: ## generate one traced request and print its trace_id
	@curl -sS -X POST http://localhost:8000/predict \
	  -H 'Content-Type: application/json' \
	  -d '{"prompt":"hello"}' | python3 -c 'import json,sys; d=json.load(sys.stdin); print("trace_id:",d.get("trace_id","?"))'

drift: ## run drift detection notebook (cli mode)
	cd 04-drift-detection && python3 scripts/drift_detect.py

demo: ## end-to-end demo (load -> alert -> trace -> drift)
	$(MAKE) load
	$(MAKE) alert
	$(MAKE) trace
	$(MAKE) drift

verify: ## rubric gate — exits 0 only if all checkpoints pass
	python3 scripts/verify.py

lint-dashboards: ## validate Grafana dashboard JSONs
	python3 scripts/lint-dashboards.py 02-prometheus-grafana/grafana/dashboards/*.json

clean: ## stop stack + remove volumes (DESTRUCTIVE)
	$(COMPOSE) down -v
