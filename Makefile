# Trida AI Blueprint — SMB Commerce Operations Agent (Meta Muse Spark)
#
# Primary workflow is uv (https://docs.astral.sh/uv/):
#   make install     uv sync --extra dev
#   make demo        generate the dataset and print the operating picture, offline
#   make test        pytest
#   make evals       eval gate, offline
#   make serve       the API on http://localhost:8000
#   make cli         the CLI surface (the ledger, as an example)
#
# No uv? python3 -m venv .venv && source .venv/bin/activate, then:
#   make install-pip

.PHONY: install install-pip demo test evals serve cli

install:
	uv sync --extra dev

install-pip:
	# The frozen, hash-verified set first, then the package itself with
	# --no-deps: pip refuses an editable install under a hashed
	# constraints file, so the two steps never mix.
	pip install -r constraints.txt
	pip install --no-deps -e .
	pip install "pytest==9.1.1" "httpx==0.28.1"

demo:
	uv run python scripts/demo.py

test:
	uv run pytest -q

evals:
	uv run python evals/run_evals.py

serve:
	uv run uvicorn smb_commerce.api:app --port 8000

cli:
	uv run python scripts/cli.py ledger
