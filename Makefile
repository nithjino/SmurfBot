.PHONY: install-deps format lint type-check full-check build up up-detach up-build up-build-detach down down-rm

install-deps:
	@echo "Installing dependencies..."
	uv venv --python 3.14 --allow-existing
	uv sync

format:
	@echo "Formatting code..."
	uv run ruff format --exit-non-zero-on-fix

lint:
	@echo "Linting code..."
	uv run ruff check

type-check:
	@echo "Checking types..."
	uv run pyright

full-check: format lint type-check
	@echo "All checks passed!"

build:
	@echo "Building Docker image..."
	docker compose build
up:
	@echo "Starting smurfbot..."
	docker compose up

up-detach:
	@echo "Starting smurfbot in detached mode..."
	docker compose up -d

up-build:
	@echo "Building and starting smurfbot..."
	docker compose up --build

up-build-detach:
	@echo "Building and starting smurfbot in detached mode..."
	docker compose up --build -d

down:
	@echo "Stopping smurfbot..."
	docker compose down

down-rm:
	@echo "Stopping and removing smurfbot containers..."
	docker compose down --rmi all --volumes --remove-orphans
