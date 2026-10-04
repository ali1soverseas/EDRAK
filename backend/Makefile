.PHONY: install lint format typecheck test check ui clean

CT_SRC = src/edrak/agents/customer_trends
CT_TESTS = tests/customer_trends
CT_PATHS = $(CT_SRC) $(CT_TESTS) evals/customer_trends

install:
	uv sync

lint:
	uv run ruff check $(CT_PATHS)
	uv run ruff format --check $(CT_PATHS)

format:
	uv run ruff check --fix $(CT_PATHS)
	uv run ruff format $(CT_PATHS)

typecheck:
	uv run mypy $(CT_SRC)

test:
	uv run pytest $(CT_TESTS)

check: lint typecheck test

ui:
	uv run streamlit run $(CT_SRC)/ui/app.py

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
