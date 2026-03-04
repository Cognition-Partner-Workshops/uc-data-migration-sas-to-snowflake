"""
Configuration loader for validation rules.

Reads validation rules from:
- validation_rule_config.json (column-level rule definitions)
- validations_list.csv (rule registry with IDs)

Merges them into a unified list of ValidationRule objects for execution.
"""

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class ValidationRule:
    """Represents a single validation rule to execute."""

    rule_id: int
    table: str
    rule_type: str  # "row_count", "distinct_count", "sum_amount", "not_null", "uniqueness"
    columns: List[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        """Human-readable rule name for reports."""
        names = {
            "row_count": "Row Count",
            "distinct_count": "Distinct Count",
            "sum_amount": "Sum Amount",
            "not_null": "Not Null",
            "uniqueness": "Uniqueness",
        }
        return names.get(self.rule_type, self.rule_type)


def _normalize_rule_type(raw: str) -> str:
    """Normalize rule type strings from different config formats."""
    mapping = {
        "row count": "row_count",
        "row_count": "row_count",
        "distinct count": "distinct_count",
        "distinct_count": "distinct_count",
        "sum amount": "sum_amount",
        "sum_amount": "sum_amount",
        "not null": "not_null",
        "not_null": "not_null",
        "uniqueness": "uniqueness",
    }
    return mapping.get(raw.lower().strip(), raw.lower().strip())


def load_json_config(config_path: Path) -> List[dict]:
    """Load validation_rule_config.json and return parsed entries."""
    with open(config_path, "r") as f:
        return json.load(f)


def load_csv_config(csv_path: Path) -> List[dict]:
    """Load validations_list.csv and return parsed entries."""
    rows: List[dict] = []
    with open(csv_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip empty rows
            if not row.get("rule", "").strip():
                continue
            rows.append(row)
    return rows


def build_validation_rules(
    json_config_path: Path,
    csv_config_path: Path,
    table_filter: Optional[str] = None,
) -> List[ValidationRule]:
    """
    Build a deduplicated, merged list of ValidationRule objects from both config sources.

    The JSON config provides column-level detail per rule type.
    The CSV config provides a rule registry.
    Rules are merged by (table, rule_type, column) to avoid duplicates.

    Args:
        json_config_path: Path to validation_rule_config.json
        csv_config_path: Path to validations_list.csv
        table_filter: Optional table name to restrict rules to.

    Returns:
        Sorted list of ValidationRule objects.
    """
    seen: set = set()
    rules: List[ValidationRule] = []
    rule_id_counter = 0

    # --- Load from JSON config ---
    json_entries = load_json_config(json_config_path)
    for entry in json_entries:
        table = entry["table"]
        if table_filter and table != table_filter:
            continue

        rule_type = _normalize_rule_type(entry["validation"])
        columns = entry.get("recommended_columns", [])

        key = (table, rule_type, tuple(sorted(columns)))
        if key not in seen:
            seen.add(key)
            rule_id_counter += 1
            rules.append(
                ValidationRule(
                    rule_id=rule_id_counter,
                    table=table,
                    rule_type=rule_type,
                    columns=columns,
                )
            )

    # --- Load from CSV config ---
    csv_entries = load_csv_config(csv_config_path)
    for entry in csv_entries:
        table = entry.get("table", "").strip()
        if table_filter and table != table_filter:
            continue

        rule_type = _normalize_rule_type(entry.get("rule", ""))
        column = entry.get("column", "NA").strip()
        columns = [] if column == "NA" else [column]

        key = (table, rule_type, tuple(sorted(columns)))
        if key not in seen:
            seen.add(key)
            rule_id_counter += 1
            rules.append(
                ValidationRule(
                    rule_id=rule_id_counter,
                    table=table,
                    rule_type=rule_type,
                    columns=columns,
                )
            )

    # Sort by table, then rule_id for deterministic ordering
    rules.sort(key=lambda r: (r.table, r.rule_id))
    return rules
