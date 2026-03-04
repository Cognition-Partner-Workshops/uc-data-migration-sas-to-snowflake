#!/usr/bin/env python3
"""
CLI entry point for the Data Validation Framework.

Validates SAS source data against Snowflake target data using configurable
rules and produces CSV + HTML comparison reports.

Usage:
    python run_validation.py \\
        --source sample_data/Scenario1 \\
        --target sample_data/Scenario2 \\
        --json-config config/validation_rule_config.json \\
        --csv-config config/validations_list.csv \\
        --output validation_output \\
        [--prefer-sas]

Options:
    --source        Path to SAS source data directory (e.g., Scenario1)
    --target        Path to Snowflake target data directory (e.g., Scenario2)
    --json-config   Path to validation_rule_config.json
    --csv-config    Path to validations_list.csv
    --output        Output directory for reports (default: validation_output)
    --prefer-sas    Prefer .sas7bdat files over CSV for source data
"""

import argparse
import logging
import sys
from pathlib import Path

from validation_framework.runner import ValidationRunner


def _configure_logging(verbose: bool = False) -> None:
    """Set up logging with a clean console format."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def parse_args(argv: list | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Data Validation Framework: SAS Source vs. Snowflake Target",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Path to SAS source data directory (e.g., sample_data/Scenario1)",
    )
    parser.add_argument(
        "--target",
        type=Path,
        required=True,
        help="Path to Snowflake target data directory (e.g., sample_data/Scenario2)",
    )
    parser.add_argument(
        "--json-config",
        type=Path,
        required=True,
        help="Path to validation_rule_config.json",
    )
    parser.add_argument(
        "--csv-config",
        type=Path,
        required=True,
        help="Path to validations_list.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("validation_output"),
        help="Output directory for reports (default: validation_output)",
    )
    parser.add_argument(
        "--prefer-sas",
        action="store_true",
        default=False,
        help="Prefer .sas7bdat files over CSV for source data loading",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose/debug logging",
    )
    return parser.parse_args(argv)


def main(argv: list | None = None) -> int:
    """
    Main entry point for the validation framework CLI.

    Returns:
        0 if all validations pass, 1 if any failures are detected.
    """
    args = parse_args(argv)
    _configure_logging(args.verbose)

    logger = logging.getLogger("run_validation")

    # Validate input paths
    for label, path in [("source", args.source), ("target", args.target)]:
        if not path.is_dir():
            logger.error("Directory not found for --%s: %s", label, path)
            return 2

    for label, path in [("json-config", args.json_config), ("csv-config", args.csv_config)]:
        if not path.is_file():
            logger.error("Config file not found for --%s: %s", label, path)
            return 2

    runner = ValidationRunner(
        source_dir=args.source,
        target_dir=args.target,
        json_config_path=args.json_config,
        csv_config_path=args.csv_config,
        output_dir=args.output,
        source_prefer_sas=args.prefer_sas,
    )

    logger.info("Starting validation run...")
    logger.info("  Source:      %s", args.source)
    logger.info("  Target:      %s", args.target)
    logger.info("  JSON Config: %s", args.json_config)
    logger.info("  CSV Config:  %s", args.csv_config)
    logger.info("  Output:      %s", args.output)
    logger.info("  Prefer SAS:  %s", args.prefer_sas)

    results = runner.run()

    # Print summary
    total = len(results)
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    skipped = sum(1 for r in results if r.status == "SKIPPED")

    logger.info("=" * 60)
    logger.info("VALIDATION COMPLETE")
    logger.info("  Total checks:  %d", total)
    logger.info("  Passed:        %d", passed)
    logger.info("  Failed:        %d", failed)
    logger.info("  Skipped:       %d", skipped)
    logger.info("=" * 60)
    logger.info("Reports saved to: %s", args.output)

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
