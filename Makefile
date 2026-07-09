VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

.DEFAULT_GOAL := help

.PHONY: help venv install lint format run menubar app restart clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

$(PY): ## Create the virtualenv
	python3 -m venv $(VENV)

venv: $(PY) ## Create the virtualenv

install: venv ## Install the package (editable) with menubar + dev extras
	$(PIP) install -e '.[menubar,dev]'
	git config core.hooksPath .githooks

lint: ## Run ruff + black checks (same as the pre-commit hook)
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .
	$(VENV)/bin/black --check src scripts

format: ## Auto-format the code
	$(VENV)/bin/ruff format .
	$(VENV)/bin/ruff check --fix .

run: ## Run the CLI (play the stream)
	$(VENV)/bin/radiomeuh

menubar: ## Run the menu bar app in the terminal (Ctrl-C to stop)
	$(PY) -m radiomeuh.menubar

app: ## (Re)build RadioMeuh.app
	./scripts/build_app.sh

restart: ## Kill a stuck stream/app and relaunch RadioMeuh.app
	./scripts/kill.sh

clean: ## Remove build/cache artifacts
	rm -rf RadioMeuh.app build dist *.egg-info src/*.egg-info
	find . -path ./$(VENV) -prune -o -name __pycache__ -exec rm -rf {} +
