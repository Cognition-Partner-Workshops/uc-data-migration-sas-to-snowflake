# SAS-to-Snowflake Migration — Validation Harness
# Usage:
#   make validate SCENARIO=Scenario1         # full validation suite
#   make validate TABLE=MONTHLY_AMB SCENARIO=Scenario1  # single table
#   make validate-all                        # all scenarios
#   make dashboard                           # launch Streamlit UI
#   make lint                                # ruff check
#   make migrate                             # generate Snowflake load artifacts

SCENARIO ?= Scenario1
TABLE ?=
PYTHON ?= python3

.PHONY: validate validate-all dashboard lint migrate

validate:
ifdef TABLE
	$(PYTHON) verify/reconcile.py --table $(TABLE) --scenario $(SCENARIO)
else
	$(PYTHON) verify/reconcile.py --scenario $(SCENARIO)
endif

validate-all:
	@fail=0; \
	for s in sample_data/Scenario*/; do \
		scenario=$$(basename "$$s"); \
		echo "\n=== Validating $$scenario ==="; \
		$(PYTHON) verify/reconcile.py --scenario "$$scenario" || fail=1; \
	done; \
	exit $$fail

dashboard:
	streamlit run app4.py

lint:
	$(PYTHON) -m ruff check . --exclude=".git,__pycache__,*.egg-info"

migrate:
	$(PYTHON) migration/sas_to_snowflake.py --all
	$(PYTHON) migration/generate_validation_sql.py
