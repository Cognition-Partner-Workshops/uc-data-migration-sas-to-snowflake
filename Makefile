# SAS-to-Snowflake Migration — Validation Harness
# Usage:
#   make validate SCENARIO=Scenario1         # full validation suite
#   make validate TABLE=MONTHLY_AMB SCENARIO=Scenario1  # single table
#   make validate-all                        # all scenarios
#   make dashboard                           # launch Streamlit UI
#   make lint                                # ruff check

SCENARIO ?= Scenario1
TABLE ?=
PYTHON ?= python3

.PHONY: validate validate-all dashboard lint

validate:
ifdef TABLE
	$(PYTHON) verify/reconcile.py --table $(TABLE) --scenario $(SCENARIO)
else
	$(PYTHON) verify/reconcile.py --scenario $(SCENARIO)
endif

validate-all:
	@for s in sample_data/Scenario*/; do \
		scenario=$$(basename "$$s"); \
		echo "\n=== Validating $$scenario ==="; \
		$(PYTHON) verify/reconcile.py --scenario "$$scenario" || true; \
	done

dashboard:
	streamlit run app4.py

lint:
	$(PYTHON) -m ruff check . --exclude=".git,__pycache__,*.egg-info"
