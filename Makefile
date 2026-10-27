# ============================================================
# Supply Chain Sentinel - Makefile
# ============================================================
# Usage: make <target>
#   make up          - Start core stack (Airflow + Postgres)
#   make up-agent    - Start everything including agent + chroma
#   make up-full     - Start everything including Langfuse
#   make down        - Stop everything
#   make logs        - Tail all logs
#   make ingest      - Run ingestion (generate + parse EDI)
#   make pipeline    - Trigger the Airflow DAG
#   make test        - Run all tests
#   make eval        - Run eval harness
#   make lint        - Ruff + mypy
#   make clean       - Remove containers + volumes
# ============================================================

COMPOSE = docker compose
COMPOSE_AGENT = docker compose --profile agent
COMPOSE_FULL = docker compose --profile agent --profile observability

.PHONY: help up up-agent up-full down logs ingest pipeline test eval lint clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

up: ## Start core stack (Airflow + Postgres)
	$(COMPOSE) up -d --build
	@echo "✅ Airflow: http://localhost:8080 (admin/admin)"
	@echo "   Postgres: localhost:5432"

up-agent: ## Start core + agent + chroma
	$(COMPOSE_AGENT) up -d --build
	@echo "✅ Airflow: http://localhost:8080"
	@echo "✅ API:     http://localhost:8000/docs"
	@echo "✅ UI:      http://localhost:8501"
	@echo "✅ Chroma:  http://localhost:8001"

up-full: ## Start everything (core + agent + Langfuse)
	$(COMPOSE_FULL) up -d --build
	@echo "✅ Airflow:  http://localhost:8080"
	@echo "✅ API:      http://localhost:8000/docs"
	@echo "✅ UI:       http://localhost:8501"
	@echo "✅ Chroma:   http://localhost:8001"
	@echo "✅ Langfuse: http://localhost:3000"

down: ## Stop all containers
	$(COMPOSE_FULL) down
	@echo "🛑 All services stopped."

logs: ## Tail logs from all services
	$(COMPOSE_FULL) logs -f --tail=100

ingest: ## Run synthetic EDI generation + ingestion
	docker compose run --rm app python -m ingestion.synthetic_generator
	docker compose run --rm app python -m ingestion.ingest_bronze
	@echo "✅ Ingestion complete. Check data/lake/bronze/"

pipeline: ## Trigger the shipment DAG in Airflow
	docker compose exec airflow-scheduler airflow dags trigger shipment_dag
	@echo "🚀 DAG triggered. Check Airflow UI at http://localhost:8080"

test: ## Run all tests
	docker compose run --rm app pytest tests/ pipeline/tests/ -v --tb=short

eval: ## Run the eval harness
	docker compose run --rm app python -m evals.run_evals
	@echo "📊 Results written to evals/results/"

lint: ## Run ruff + mypy
	docker compose run --rm app ruff check .
	docker compose run --rm app mypy agent/ api/ --ignore-missing-imports

clean: ## Remove containers AND volumes (destructive!)
	$(COMPOSE_FULL) down -v --remove-orphans
	rm -rf data/lake/
	@echo "🗑️  All containers, volumes, and lake data removed."   
