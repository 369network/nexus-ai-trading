# =============================================================================
# NEXUS ALPHA — Makefile
# =============================================================================

.PHONY: install setup-db run-paper run-live run-dashboard run-backtest \
        run-dream test lint format docker-up docker-down health-check \
        logs deploy-dashboard clean help

PYTHON := python
POETRY := poetry
COMPOSE := docker compose
COMPOSE_DEV := docker compose -f docker-compose.yml -f docker-compose.dev.yml

# Default target
.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

install: ## Install all Python dependencies via Poetry
	@echo "[nexus] Installing dependencies..."
	$(POETRY) install
	@echo "[nexus] Done. Activate with: poetry shell"

setup-db: ## Run Supabase SQL setup script against the configured DATABASE_URL
	@echo "[nexus] Setting up database schema..."
	@if [ -z "$$DATABASE_URL" ]; then \
		echo "ERROR: DATABASE_URL not set. Check your .env file."; \
		exit 1; \
	fi
	$(POETRY) run python -c "\
import asyncio, asyncpg, os; \
sql = open('scripts/setup_supabase.sql').read(); \
async def run(): \
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql')); \
    await conn.execute(sql); \
    await conn.close(); \
    print('[nexus] Database schema applied.'); \
asyncio.run(run())"

# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

run-paper: ## Run the trading bot in paper mode
	@echo "[nexus] Starting in PAPER mode..."
	PAPER_MODE=true ENVIRONMENT=paper $(POETRY) run python -m src.main

run-live: ## Run the trading bot in LIVE mode (requires confirmation)
	@echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
	@echo "  WARNING: This starts LIVE trading with REAL MONEY  "
	@echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
	@read -p "Type 'CONFIRM_LIVE_TRADING' to proceed: " confirm; \
	if [ "$$confirm" = "CONFIRM_LIVE_TRADING" ]; then \
		PAPER_MODE=false ENVIRONMENT=live $(POETRY) run python -m src.main; \
	else \
		echo "Aborted."; \
	fi

run-dashboard: ## Run the Next.js dashboard (requires dashboard/ directory)
	@echo "[nexus] Starting dashboard..."
	cd dashboard && npm run dev

run-backtest: ## Run backtesting engine
	@echo "[nexus] Starting backtester..."
	$(POETRY) run python -m src.backtest.runner

run-dream: ## Run overnight Dream Mode (strategy discovery)
	@echo "[nexus] Starting Dream Mode..."
	$(POETRY) run python -m src.agents.dream_mode

# ---------------------------------------------------------------------------
# Code Quality
# ---------------------------------------------------------------------------

test: ## Run test suite with coverage
	@echo "[nexus] Running tests..."
	$(POETRY) run pytest tests/ -v --cov=src --cov-report=term-missing --cov-report=html

lint: ## Run ruff linter and mypy type checker
	@echo "[nexus] Running ruff..."
	$(POETRY) run ruff check src/ tests/
	@echo "[nexus] Running mypy..."
	$(POETRY) run mypy src/

format: ## Auto-format code with black and ruff --fix
	@echo "[nexus] Formatting with black..."
	$(POETRY) run black src/ tests/
	@echo "[nexus] Fixing with ruff..."
	$(POETRY) run ruff check --fix src/ tests/

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

docker-up: ## Start all Docker services (production stack)
	@echo "[nexus] Starting Docker stack..."
	$(COMPOSE) up -d
	@echo "[nexus] Stack running. Check health with: make health-check"

docker-up-dev: ## Start Docker services with dev overrides (hot reload)
	@echo "[nexus] Starting Docker dev stack..."
	$(COMPOSE_DEV) up -d

docker-down: ## Stop all Docker services
	@echo "[nexus] Stopping Docker stack..."
	$(COMPOSE) down

docker-down-volumes: ## Stop Docker services AND remove volumes (destructive!)
	@echo "[nexus] Stopping and removing all volumes..."
	@read -p "This deletes all data volumes. Type 'yes' to confirm: " confirm; \
	if [ "$$confirm" = "yes" ]; then \
		$(COMPOSE) down -v; \
	else \
		echo "Aborted."; \
	fi

docker-build: ## Rebuild all Docker images
	@echo "[nexus] Building images..."
	$(COMPOSE) build --no-cache

docker-build-dashboard: ## Build dashboard Docker image
	@echo "[nexus] Building dashboard image..."
	docker build -f Dockerfile.dashboard -t nexus-alpha/dashboard:latest .

# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

health-check: ## Check health of all running services
	@echo "[nexus] Checking bot health..."
	@curl -sf http://localhost:8080/health | python -m json.tool || echo "Bot: UNHEALTHY"
	@echo "\n[nexus] Checking data service health..."
	@curl -sf http://localhost:8081/health | python -m json.tool || echo "Data: UNHEALTHY"
	@echo "\n[nexus] Checking Redis..."
	@docker exec nexus-redis redis-cli ping || echo "Redis: UNHEALTHY"
	@echo "\n[nexus] Checking Prometheus..."
	@curl -sf http://localhost:9090/-/healthy || echo "Prometheus: UNHEALTHY"
	@echo "\n[nexus] Checking Grafana..."
	@curl -sf http://localhost:3001/api/health | python -m json.tool || echo "Grafana: UNHEALTHY"

logs: ## Tail logs from nexus-bot (last 100 lines + follow)
	$(COMPOSE) logs -f --tail=100 nexus-bot

logs-data: ## Tail logs from nexus-data
	$(COMPOSE) logs -f --tail=100 nexus-data

logs-all: ## Tail logs from all services
	$(COMPOSE) logs -f --tail=50

deploy-dashboard: ## Deploy dashboard to Vercel
	@echo "[nexus] Deploying dashboard to Vercel..."
	cd dashboard && npx vercel --prod

# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

clean: ## Remove Python cache files and build artifacts
	@echo "[nexus] Cleaning cache..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true
	find . -type f -name "*.pyc" -delete 2>/dev/null; true
	find . -type f -name "*.pyo" -delete 2>/dev/null; true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	@echo "[nexus] Done."

env-check: ## Validate that all required env vars are set
	@echo "[nexus] Checking environment variables..."
	$(POETRY) run python -c "\
from config.settings import get_settings; \
s = get_settings(); \
print(f'Environment: {s.environment}'); \
print(f'Paper mode: {s.paper_mode}'); \
print(f'Log level: {s.log_level}'); \
print('Settings loaded successfully.')"

help: ## Show this help message
	@echo "NEXUS ALPHA — Available make targets:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-25s\033[0m %s\n", $$1, $$2}'
	@echo ""
