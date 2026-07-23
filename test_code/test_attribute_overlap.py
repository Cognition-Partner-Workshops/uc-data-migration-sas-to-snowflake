import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lineage.attribute_overlap import compute_attribute_overlap

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAS_LINEAGE = os.path.join(REPO_ROOT, "lineage", "SAS_lineage.json")
SF_LINEAGE = os.path.join(REPO_ROOT, "lineage", "SF_lineage.json")
DATA_DIR = os.path.join(REPO_ROOT, "sample_data")

EXPECTED_ATTRIBUTES = [
    {"attribute": "account_id", "tables": ["CUST_ACCOUNTS", "DAILY_BALANCE", "MONTHLY_AMB"], "platforms": ["SAS", "Snowflake"], "table_count": 3},
    {"attribute": "account_type", "tables": ["CUST_ACCOUNTS"], "platforms": ["SAS", "Snowflake"], "table_count": 1},
    {"attribute": "average_monthly_balance", "tables": ["MONTHLY_AMB"], "platforms": ["SAS", "Snowflake"], "table_count": 1},
    {"attribute": "customer_id", "tables": ["CUST_ACCOUNTS", "DAILY_BALANCE", "MONTHLY_AMB"], "platforms": ["SAS", "Snowflake"], "table_count": 3},
    {"attribute": "date", "tables": ["DAILY_BALANCE"], "platforms": ["SAS", "Snowflake"], "table_count": 1},
    {"attribute": "date_computed", "tables": ["MONTHLY_AMB"], "platforms": ["SAS", "Snowflake"], "table_count": 1},
    {"attribute": "end_date", "tables": ["CUST_ACCOUNTS"], "platforms": ["SAS", "Snowflake"], "table_count": 1},
    {"attribute": "end_of_day_balance", "tables": ["DAILY_BALANCE"], "platforms": ["SAS", "Snowflake"], "table_count": 1},
    {"attribute": "is_active", "tables": ["CUST_ACCOUNTS"], "platforms": ["SAS", "Snowflake"], "table_count": 1},
    {"attribute": "month", "tables": ["DAILY_BALANCE"], "platforms": ["SAS", "Snowflake"], "table_count": 1},
    {"attribute": "reporting_month_yyyymm", "tables": ["MONTHLY_AMB"], "platforms": ["SAS", "Snowflake"], "table_count": 1},
    {"attribute": "start_date", "tables": ["CUST_ACCOUNTS"], "platforms": ["SAS", "Snowflake"], "table_count": 1},
]

EXPECTED_OVERLAP_MATRIX = {
    "tables": ["CUST_ACCOUNTS", "DAILY_BALANCE", "MONTHLY_AMB"],
    "shared_counts": [[6, 2, 2], [2, 5, 2], [2, 2, 5]],
    "jaccard": [[1.0, 0.2222, 0.2222], [0.2222, 1.0, 0.25], [0.2222, 0.25, 1.0]],
}

EXPECTED_DOMAINS = [
    {
        "domain": "account_id / customer_id",
        "tables": ["CUST_ACCOUNTS", "DAILY_BALANCE", "MONTHLY_AMB"],
        "key_attributes": ["account_id", "customer_id"],
        "attributes": [
            "account_id", "account_type", "average_monthly_balance", "customer_id",
            "date", "date_computed", "end_date", "end_of_day_balance", "is_active",
            "month", "reporting_month_yyyymm", "start_date",
        ],
    }
]


def result():
    return compute_attribute_overlap(SAS_LINEAGE, SF_LINEAGE, DATA_DIR)


def test_top_level_keys():
    r = result()
    assert set(r.keys()) == {"attributes", "overlap_matrix", "suggested_domains"}


def test_attributes_ground_truth():
    assert result()["attributes"] == EXPECTED_ATTRIBUTES


def test_attributes_structure():
    for rec in result()["attributes"]:
        assert set(rec.keys()) == {"attribute", "tables", "platforms", "table_count"}
        assert isinstance(rec["attribute"], str)
        assert rec["attribute"] == rec["attribute"].lower()
        assert rec["tables"] == sorted(rec["tables"])
        assert rec["platforms"] == sorted(rec["platforms"])
        assert set(rec["platforms"]) <= {"SAS", "Snowflake"}
        assert rec["table_count"] == len(rec["tables"])
    attrs = [rec["attribute"] for rec in result()["attributes"]]
    assert attrs == sorted(attrs)


def test_overlap_matrix_ground_truth():
    assert result()["overlap_matrix"] == EXPECTED_OVERLAP_MATRIX


def test_overlap_matrix_structure():
    m = result()["overlap_matrix"]
    n = len(m["tables"])
    assert m["tables"] == sorted(m["tables"])
    assert len(m["shared_counts"]) == n and all(len(row) == n for row in m["shared_counts"])
    assert len(m["jaccard"]) == n and all(len(row) == n for row in m["jaccard"])
    for i in range(n):
        assert m["jaccard"][i][i] == 1.0
        for j in range(n):
            assert isinstance(m["shared_counts"][i][j], int)
            assert isinstance(m["jaccard"][i][j], float)


def test_suggested_domains_ground_truth():
    assert result()["suggested_domains"] == EXPECTED_DOMAINS


def test_scenario_subfolder_as_data_dir():
    r = compute_attribute_overlap(
        SAS_LINEAGE, SF_LINEAGE, os.path.join(DATA_DIR, "Scenario1")
    )
    assert set(r.keys()) == {"attributes", "overlap_matrix", "suggested_domains"}
    assert r["overlap_matrix"]["tables"] == ["CUST_ACCOUNTS", "DAILY_BALANCE", "MONTHLY_AMB"]
    assert r["attributes"] == EXPECTED_ATTRIBUTES
    assert r["overlap_matrix"] == EXPECTED_OVERLAP_MATRIX
    assert r["suggested_domains"] == EXPECTED_DOMAINS
