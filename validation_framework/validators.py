"""
Validation rule implementations for SAS-to-Snowflake data migration.

Each validator function takes source and target DataFrames along with
rule parameters and returns a ValidationResult with pass/fail status
and delta values for comparison reporting.
"""

from dataclasses import dataclass
from typing import List, Optional, Union

import pandas as pd

from validation_framework.config_loader import ValidationRule


@dataclass
class ValidationResult:
    """Result of executing a single validation rule."""

    rule_id: int
    table: str
    rule_type: str
    rule_display_name: str
    column: str
    source_value: Union[int, float, bool, str]
    target_value: Union[int, float, bool, str]
    delta: Union[int, float, str]
    status: str  # "PASS", "FAIL", "SKIPPED"
    detail: str = ""


def _safe_column_lookup(df: pd.DataFrame, column: str) -> Optional[str]:
    """
    Find a column in the DataFrame, case-insensitive.

    Returns the actual column name if found, None otherwise.
    """
    col_map = {c.lower(): c for c in df.columns}
    return col_map.get(column.lower())


def validate_row_count(
    rule: ValidationRule,
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
) -> List[ValidationResult]:
    """
    Validate that source and target have the same row count.

    Returns a single result comparing total row counts.
    """
    source_count = len(source_df)
    target_count = len(target_df)
    delta = source_count - target_count
    status = "PASS" if delta == 0 else "FAIL"

    return [
        ValidationResult(
            rule_id=rule.rule_id,
            table=rule.table,
            rule_type=rule.rule_type,
            rule_display_name=rule.display_name,
            column="*",
            source_value=source_count,
            target_value=target_count,
            delta=delta,
            status=status,
            detail=f"Source rows: {source_count}, Target rows: {target_count}",
        )
    ]


def validate_distinct_count(
    rule: ValidationRule,
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
) -> List[ValidationResult]:
    """
    Validate that distinct counts match for specified columns.

    Returns one result per column in the rule.
    """
    results: List[ValidationResult] = []

    for col_name in rule.columns:
        src_col = _safe_column_lookup(source_df, col_name)
        tgt_col = _safe_column_lookup(target_df, col_name)

        if src_col is None or tgt_col is None:
            results.append(
                ValidationResult(
                    rule_id=rule.rule_id,
                    table=rule.table,
                    rule_type=rule.rule_type,
                    rule_display_name=rule.display_name,
                    column=col_name,
                    source_value="N/A",
                    target_value="N/A",
                    delta="N/A",
                    status="SKIPPED",
                    detail=f"Column '{col_name}' not found in "
                    f"{'source' if src_col is None else 'target'}",
                )
            )
            continue

        src_distinct = int(source_df[src_col].nunique())
        tgt_distinct = int(target_df[tgt_col].nunique())
        delta = src_distinct - tgt_distinct
        status = "PASS" if delta == 0 else "FAIL"

        results.append(
            ValidationResult(
                rule_id=rule.rule_id,
                table=rule.table,
                rule_type=rule.rule_type,
                rule_display_name=rule.display_name,
                column=col_name,
                source_value=src_distinct,
                target_value=tgt_distinct,
                delta=delta,
                status=status,
                detail=f"Distinct '{col_name}': source={src_distinct}, target={tgt_distinct}",
            )
        )

    return results


def validate_sum_amount(
    rule: ValidationRule,
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
    tolerance: float = 0.01,
) -> List[ValidationResult]:
    """
    Validate that the sum of numeric columns matches between source and target.

    Uses a configurable tolerance for floating-point comparison.
    Returns one result per column.
    """
    results: List[ValidationResult] = []

    for col_name in rule.columns:
        src_col = _safe_column_lookup(source_df, col_name)
        tgt_col = _safe_column_lookup(target_df, col_name)

        if src_col is None or tgt_col is None:
            results.append(
                ValidationResult(
                    rule_id=rule.rule_id,
                    table=rule.table,
                    rule_type=rule.rule_type,
                    rule_display_name=rule.display_name,
                    column=col_name,
                    source_value="N/A",
                    target_value="N/A",
                    delta="N/A",
                    status="SKIPPED",
                    detail=f"Column '{col_name}' not found in "
                    f"{'source' if src_col is None else 'target'}",
                )
            )
            continue

        src_sum = float(pd.to_numeric(source_df[src_col], errors="coerce").sum())
        tgt_sum = float(pd.to_numeric(target_df[tgt_col], errors="coerce").sum())
        delta = round(src_sum - tgt_sum, 4)
        status = "PASS" if abs(delta) < tolerance else "FAIL"

        results.append(
            ValidationResult(
                rule_id=rule.rule_id,
                table=rule.table,
                rule_type=rule.rule_type,
                rule_display_name=rule.display_name,
                column=col_name,
                source_value=round(src_sum, 4),
                target_value=round(tgt_sum, 4),
                delta=delta,
                status=status,
                detail=f"Sum '{col_name}': source={src_sum:.4f}, target={tgt_sum:.4f}",
            )
        )

    return results


def validate_not_null(
    rule: ValidationRule,
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
) -> List[ValidationResult]:
    """
    Validate that specified columns have zero null values in both source and target.

    A column passes if both source and target have zero nulls.
    Returns one result per column.
    """
    results: List[ValidationResult] = []

    for col_name in rule.columns:
        src_col = _safe_column_lookup(source_df, col_name)
        tgt_col = _safe_column_lookup(target_df, col_name)

        if src_col is None or tgt_col is None:
            results.append(
                ValidationResult(
                    rule_id=rule.rule_id,
                    table=rule.table,
                    rule_type=rule.rule_type,
                    rule_display_name=rule.display_name,
                    column=col_name,
                    source_value="N/A",
                    target_value="N/A",
                    delta="N/A",
                    status="SKIPPED",
                    detail=f"Column '{col_name}' not found in "
                    f"{'source' if src_col is None else 'target'}",
                )
            )
            continue

        src_nulls = int(source_df[src_col].isna().sum())
        tgt_nulls = int(target_df[tgt_col].isna().sum())
        delta = src_nulls - tgt_nulls
        # Pass only if both sides have zero nulls
        status = "PASS" if src_nulls == 0 and tgt_nulls == 0 else "FAIL"

        results.append(
            ValidationResult(
                rule_id=rule.rule_id,
                table=rule.table,
                rule_type=rule.rule_type,
                rule_display_name=rule.display_name,
                column=col_name,
                source_value=src_nulls,
                target_value=tgt_nulls,
                delta=delta,
                status=status,
                detail=f"Nulls in '{col_name}': source={src_nulls}, target={tgt_nulls}",
            )
        )

    return results


def validate_uniqueness(
    rule: ValidationRule,
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
) -> List[ValidationResult]:
    """
    Validate uniqueness of values in specified columns.

    Checks that each column's values are unique (no duplicates) in both
    source and target datasets.
    Returns one result per column.
    """
    results: List[ValidationResult] = []

    for col_name in rule.columns:
        src_col = _safe_column_lookup(source_df, col_name)
        tgt_col = _safe_column_lookup(target_df, col_name)

        if src_col is None or tgt_col is None:
            results.append(
                ValidationResult(
                    rule_id=rule.rule_id,
                    table=rule.table,
                    rule_type=rule.rule_type,
                    rule_display_name=rule.display_name,
                    column=col_name,
                    source_value="N/A",
                    target_value="N/A",
                    delta="N/A",
                    status="SKIPPED",
                    detail=f"Column '{col_name}' not found in "
                    f"{'source' if src_col is None else 'target'}",
                )
            )
            continue

        src_is_unique = bool(source_df[src_col].is_unique)
        tgt_is_unique = bool(target_df[tgt_col].is_unique)
        src_dup_count = int(source_df[src_col].duplicated().sum())
        tgt_dup_count = int(target_df[tgt_col].duplicated().sum())
        status = "PASS" if src_is_unique and tgt_is_unique else "FAIL"

        results.append(
            ValidationResult(
                rule_id=rule.rule_id,
                table=rule.table,
                rule_type=rule.rule_type,
                rule_display_name=rule.display_name,
                column=col_name,
                source_value=src_dup_count,
                target_value=tgt_dup_count,
                delta=src_dup_count - tgt_dup_count,
                status=status,
                detail=(
                    f"Duplicates in '{col_name}': "
                    f"source={src_dup_count}, target={tgt_dup_count}"
                ),
            )
        )

    return results


# Registry mapping rule types to their validator functions
VALIDATOR_REGISTRY = {
    "row_count": validate_row_count,
    "distinct_count": validate_distinct_count,
    "sum_amount": validate_sum_amount,
    "not_null": validate_not_null,
    "uniqueness": validate_uniqueness,
}


def execute_rule(
    rule: ValidationRule,
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
) -> List[ValidationResult]:
    """
    Execute a single validation rule using the appropriate validator.

    Args:
        rule: The validation rule to execute.
        source_df: SAS source DataFrame.
        target_df: Snowflake target DataFrame.

    Returns:
        List of ValidationResult objects (one per column checked, or one for row_count).

    Raises:
        ValueError: If the rule_type is not recognized.
    """
    validator_fn = VALIDATOR_REGISTRY.get(rule.rule_type)
    if validator_fn is None:
        raise ValueError(
            f"Unknown rule type '{rule.rule_type}'. "
            f"Supported: {list(VALIDATOR_REGISTRY.keys())}"
        )
    return validator_fn(rule, source_df, target_df)
