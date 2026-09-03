# Amon Hen — common tasks.
#
# Everything here works with nothing installed but Docker. The `local-*` targets
# are for running the backend directly on your machine, which is usually nicer
# for iterating on the science code.

.DEFAULT_GOAL := help
.PHONY: help up down logs restart build rebuild build-eo boundary fixtures test lint api-shell \
        local-api local-test clean status

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

up: ## Start the platform (http://localhost:3000)
	docker compose up -d
	@echo "\n  UI   http://localhost:3000"
	@echo "  API  http://localhost:8000/docs\n"

down: ## Stop everything
	docker compose down

logs: ## Tail all logs
	docker compose logs -f

restart: ## Restart both services
	docker compose restart

build: ## Build images
	docker compose build

rebuild: ## Rebuild images from scratch
	docker compose build --no-cache

build-eo: ## Rebuild the API image with Sentinel-2 raster support ([eo] extra)
	@# Turns on burned-area mapping from dNBR and live fuel moisture from NDMI.
	@# Adds 165 MB (GDAL, via rasterio) and a couple of minutes to the build.
	docker compose build --build-arg INSTALL_EO=true api
	docker compose up -d api
	@echo "\n  Sentinel-2 imagery enabled. Check with:"
	@echo "  curl -s localhost:8000/api/v1/system/status | grep -A3 sentinel2\n"

status: ## Show service status and data source modes
	@docker compose ps
	@echo ""
	@curl -s http://localhost:8000/api/v1/system/status | python3 -m json.tool 2>/dev/null \
	  || echo "API not reachable"

boundary: ## Rebuild the Greece boundary polygon from Natural Earth
	docker compose exec api python /app/scripts/build_boundary.py

fixtures: ## Regenerate the bundled demo dataset
	cd backend && python3 scripts/make_fixtures.py

test: ## Run the backend test suite in Docker
	@# tests/ is deliberately not baked into the runtime image, so mount the
	@# source over /app and install the test deps just for this run.
	docker run --rm -v "$(PWD)/backend:/app" -v "$(PWD)/data:/data" \
	  -e AMONHEN_DATA_DIR=/data -w /app amonhen-api \
	  sh -c "uv pip install --system --no-cache -q pytest pytest-asyncio \
	         && python -m pytest tests -q"

lint: ## Ruff + mypy over the backend
	cd backend && ruff check amonhen && ruff format --check amonhen

api-shell: ## Open a shell in the API container
	docker compose exec api bash

local-api: ## Run the API directly (needs: uv)
	cd backend && uv run --with-editable . uvicorn amonhen.main:app --reload

local-test: ## Run tests directly (needs: uv)
	cd backend && PYTHONPATH=. uv run --with pytest --with pytest-asyncio \
	  --with pydantic --with pydantic-settings --with numpy --with scikit-learn \
	  --with shapely --with structlog --with httpx --with tenacity \
	  python -m pytest tests -q

clean: ## Remove caches and containers
	docker compose down -v
	rm -rf data/cache/* backend/.pytest_cache backend/.ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
