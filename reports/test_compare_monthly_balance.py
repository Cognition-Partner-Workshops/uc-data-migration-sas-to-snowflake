import json

import pytest
from compare_monthly_balance import ReportParser, compare, differences
from monthly_balance_summary import render, summarise
from test_monthly_balance_summary import _summary_frame


def test_compare_full_rows_and_embedded_details(tmp_path):
    summary = summarise(_summary_frame())
    paths = [tmp_path / "legacy.html", tmp_path / "snowflake.html"]
    for path in paths:
        path.write_text(render(summary, path.stem, path.stem), encoding="utf-8")
        path.with_suffix(".json").write_text(json.dumps({
            "rows": [{"account": "A1", "balance": 10}], "summary": summary,
            "commit": "abc", "input_digest": "frozen"}))
    result = compare(*paths)
    assert result["rows"] == result["summary"] == result["embedded"] == result["visible"] == []
    assert len(result["header"]) == 1
    altered = json.loads(paths[1].with_suffix(".json").read_text())
    altered["rows"][0]["balance"] = 11
    paths[1].with_suffix(".json").write_text(json.dumps(altered))
    paths[1].write_text(paths[1].read_text().replace('"balance": 100.0', '"balance": 101.0'))
    result = compare(*paths)
    assert result["rows"] == [{"path": "rows/0/balance", "legacy": 10, "snowflake": 11}]
    assert result["embedded"][0]["path"] == "embedded/details/1001/0/balance"


def test_empty_and_missing_differences():
    assert differences([], []) == []
    assert differences([1], []) == [{"path": "/0", "legacy": 1, "snowflake": None}]
    parser = ReportParser()
    parser.feed('<style>hidden</style><header> source </header><p>total</p>')
    assert parser.header == ["source"]
    assert parser.visible == ["total"]
    with pytest.raises(json.JSONDecodeError):
        parser.feed('<script id="details">invalid</script>')
