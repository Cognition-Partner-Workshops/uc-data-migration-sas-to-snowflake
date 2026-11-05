"""Compare full report evidence and visible HTML, preserving every difference."""

import argparse
import json
from html.parser import HTMLParser
from pathlib import Path

from monthly_balance_summary import outside_repo


class ReportParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = False
        self.script_id = None
        self.visible = []
        self.header = []
        self.in_header = False
        self.embedded = {}

    def handle_starttag(self, tag, attrs):
        if tag == "header":
            self.in_header = True
        if tag in ("style", "script", "title"):
            self.hidden = True
        if tag == "script":
            self.script_id = dict(attrs).get("id")

    def handle_endtag(self, tag):
        if tag == "header":
            self.in_header = False
        if tag in ("style", "script", "title"):
            self.hidden = False
            self.script_id = None

    def handle_data(self, data):
        if self.script_id:
            self.embedded[self.script_id] = json.loads(data)
        elif not self.hidden and data.strip():
            target = self.header if self.in_header else self.visible
            target.append(" ".join(data.split()))


def differences(left, right, path=""):
    if isinstance(left, dict) and isinstance(right, dict):
        return [difference for key in sorted(left.keys() | right.keys())
                for difference in differences(left.get(key), right.get(key), f"{path}/{key}")]
    if isinstance(left, list) and isinstance(right, list):
        return [difference for index in range(max(len(left), len(right)))
                for difference in differences(left[index] if index < len(left) else None,
                                              right[index] if index < len(right) else None,
                                              f"{path}/{index}")]
    return [] if left == right else [{"path": path, "legacy": left, "snowflake": right}]


def compare(legacy, snowflake):
    sources = []
    for path in (legacy, snowflake):
        parser = ReportParser()
        parser.feed(path.read_text(encoding="utf-8"))
        evidence = json.loads(path.with_suffix(".json").read_text())
        sources.append({"visible": parser.visible, "header": " ".join(parser.header),
                        "embedded": parser.embedded, **evidence})
    left, right = sources
    result = {key: differences(left[key], right[key], key)
              for key in ("rows", "summary", "embedded", "visible", "header", "commit", "input_digest")}
    result["counts"] = {"legacy_accounts": len(left["rows"]),
                        "snowflake_accounts": len(right["rows"]),
                        "legacy_customers": len(left["summary"]["ranks"]),
                        "snowflake_customers": len(right["summary"]["ranks"])}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy", type=Path)
    parser.add_argument("snowflake", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.legacy, args.snowflake)
    output = outside_repo(args.out)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
