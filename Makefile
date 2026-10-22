# SAS-to-Snowflake Migration — Validation Harness
# Usage:
#   make validate SCENARIO=Scenario1         # full validation suite
#   make validate TABLE=MONTHLY_AMB SCENARIO=Scenario1  # single table
#   make validate-all                        # all scenarios
#   make run-job JOB=JOB03_CALC_AMB INPUTS=CUST_ACCOUNTS,DAILY_BALANCE OUTPUT=MONTHLY_AMB SCENARIO=Scenario1 RUN_DATE=2025-08-01
#   make dashboard                           # launch Streamlit UI
#   make lint                                # ruff check

SCENARIO ?= Scenario1
TABLE ?=
PYTHON ?= python3
JOB ?= JOB03_CALC_AMB
INPUTS ?= CUST_ACCOUNTS,DAILY_BALANCE
OUTPUT ?= MONTHLY_AMB
RUN_DATE ?=

.PHONY: validate validate-all run-job dashboard lint

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

run-job:
	$(PYTHON) verify/run_snowflake_job.py --job $(JOB) --inputs $(INPUTS) --output $(OUTPUT) --scenario $(SCENARIO) $(if $(RUN_DATE),--run-date $(RUN_DATE),)

dashboard:
	streamlit run app4.py

lint:
	$(PYTHON) -m ruff check . --exclude=".git,__pycache__,*.egg-info"
