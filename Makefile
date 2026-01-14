.PHONY: test dev build lint format help

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  test    Run all tests"
	@echo "  dev     Run the application in debug mode"
	@echo "  build   Build the package"
	@echo "  lint    Run linting"
	@echo "  format  Format code"

test:
	poetry run pytest

dev:
	poetry run lxmf-chat --debug

build:
	poetry build

lint:
	poetry run ruff check .

format:
	poetry run ruff format .

