"""
Validation runner that orchestrates the full validation pipeline.

Coordinates:
1. Loading configuration rules
2. Discovering and loading source/target data
3. Executing validation rules against each table
4. Collecting results and generating reports
"""

import logging
from pathlib import Path
from typing import List, Optional

from validation_framework.config_loader import ValidationRule, build_validation_rules
from validation_framework.data_reader import discover_tables, load_source_and_target
from validation_framework.report_generator import (
    generate_csv_report,
    generate_html_report,
)
from validation_framework.validators import ValidationResult, execute_rule

logger = logging.getLogger(__name__)


class ValidationRunner:
    """
    Orchestrates data validation between SAS source and Snowflake target datasets.

    Attributes:
        source_dir: Path to the SAS source data directory.
        target_dir: Path to the Snowflake target data directory.
        json_config_path: Path to validation_rule_config.json.
        csv_config_path: Path to validations_list.csv.
        output_dir: Path for writing output reports.
        source_prefer_sas: Whether to prefer .sas7bdat files for source data.
    """

    def __init__(
        self,
        source_dir: Path,
        target_dir: Path,
        json_config_path: Path,
        csv_config_path: Path,
        output_dir: Path,
        source_prefer_sas: bool = False,
    ) -> None:
        self.source_dir = source_dir
        self.target_dir = target_dir
        self.json_config_path = json_config_path
        self.csv_config_path = csv_config_path
        self.output_dir = output_dir
        self.source_prefer_sas = source_prefer_sas

    def _load_rules(
        self, table_filter: Optional[str] = None
    ) -> List[ValidationRule]:
        """Load and merge validation rules from config files."""
        rules = build_validation_rules(
            self.json_config_path,
            self.csv_config_path,
            table_filter=table_filter,
        )
        logger.info("Loaded %d validation rules", len(rules))
        return rules

    def _discover_common_tables(self) -> List[str]:
        """Find tables that exist in both source and target directories."""
        source_tables = set(discover_tables(self.source_dir))
        target_tables = set(discover_tables(self.target_dir))
        common = sorted(source_tables & target_tables)
        logger.info(
            "Source tables: %s, Target tables: %s, Common: %s",
            source_tables,
            target_tables,
            common,
        )
        return common

    def _generate_implicit_rules(
        self, table_name: str, existing_rules: List[ValidationRule]
    ) -> List[ValidationRule]:
        """
        Generate row_count rules for tables that have no explicit rules.

        Every table should at least get a row count validation even if
        not specified in the config files.
        """
        existing_tables_with_rules = {r.table for r in existing_rules}
        if table_name in existing_tables_with_rules:
            return []

        max_id = max((r.rule_id for r in existing_rules), default=0)
        return [
            ValidationRule(
                rule_id=max_id + 1,
                table=table_name,
                rule_type="row_count",
                columns=[],
            )
        ]

    def run(self) -> List[ValidationResult]:
        """
        Execute the full validation pipeline.

        Steps:
        1. Discover common tables between source and target
        2. Load validation rules from config
        3. For each table, load data and execute applicable rules
        4. Generate CSV and HTML reports
        5. Return all results

        Returns:
            List of all ValidationResult objects from the run.
        """
        all_results: List[ValidationResult] = []
        common_tables = self._discover_common_tables()

        if not common_tables:
            logger.warning("No common tables found between source and target directories.")
            return all_results

        # Load all configured rules
        configured_rules = self._load_rules()

        for table_name in common_tables:
            logger.info("--- Validating table: %s ---", table_name)

            # Get rules for this table
            table_rules = [r for r in configured_rules if r.table == table_name]

            # Add implicit row_count rule if no rules are configured for this table
            implicit_rules = self._generate_implicit_rules(table_name, configured_rules)
            table_rules.extend(implicit_rules)

            if not table_rules:
                logger.info("No validation rules for table %s, skipping.", table_name)
                continue

            # Load source and target data
            try:
                data = load_source_and_target(
                    self.source_dir,
                    self.target_dir,
                    table_name,
                    source_prefer_sas=self.source_prefer_sas,
                )
            except FileNotFoundError as e:
                logger.error("Could not load data for %s: %s", table_name, e)
                continue

            source_df = data["source"]
            target_df = data["target"]
            logger.info(
                "Loaded %s: source=%d rows, target=%d rows",
                table_name,
                len(source_df),
                len(target_df),
            )

            # Execute each rule
            for rule in table_rules:
                try:
                    results = execute_rule(rule, source_df, target_df)
                    all_results.extend(results)
                    for r in results:
                        log_fn = logger.info if r.status == "PASS" else logger.warning
                        log_fn(
                            "[%s] %s on '%s': %s (delta=%s)",
                            r.status,
                            r.rule_display_name,
                            r.column,
                            r.detail,
                            r.delta,
                        )
                except ValueError as e:
                    logger.error("Error executing rule %s: %s", rule, e)

        # Generate reports
        self._write_reports(all_results)

        return all_results

    def _write_reports(self, results: List[ValidationResult]) -> None:
        """Write CSV and HTML reports to the output directory."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        csv_path = self.output_dir / "validation_summary.csv"
        generate_csv_report(results, csv_path)
        logger.info("CSV report written to: %s", csv_path)

        html_path = self.output_dir / "validation_report.html"
        generate_html_report(results, html_path)
        logger.info("HTML report written to: %s", html_path)
