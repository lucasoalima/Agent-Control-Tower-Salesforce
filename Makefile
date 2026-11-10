.DEFAULT_GOAL := help
.PHONY: help up down seed simulate logs db backend frontend test test-backend test-frontend lint

help: ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-15s %s\n", $$1, $$2}'

# -- Docker Compose (everything in containers) --------------------------------
up: ## Build and start database, API and dashboard
	docker compose up --build -d
down: ## Stop the stack (keeps the database volume)
	docker compose down
seed: ## Create environments, policies, demo agents and the operator (prints credentials once)
	docker compose exec backend act seed
simulate: ## Send 16 demo tool requests through the gateway
	docker compose exec backend act simulate
logs: ## Follow API logs
	docker compose logs -f backend

# -- Running on the host (database in Docker) ---------------------------------
db: ## Start only PostgreSQL
	docker compose up -d db
backend: ## Run the API with reload (needs `make db`)
	cd backend && uv run act migrate && uv run uvicorn control_tower.main:app --reload --no-access-log
frontend: ## Run the dashboard in dev mode
	cd frontend && npm run dev

# -- Checks -------------------------------------------------------------------
test: test-backend test-frontend ## Run every test suite
test-backend: ## Lint, type-check and test the backend (needs PostgreSQL)
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy && ACT_REQUIRE_DB=1 uv run pytest
test-frontend: ## Lint, type-check, test and build the dashboard
	cd frontend && npm run lint && npm run typecheck && npm test && npm run build
