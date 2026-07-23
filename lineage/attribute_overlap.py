"""Attribute overlap analysis across lineage tables (DJ-48).

Computes per-attribute table membership, a pairwise table overlap matrix,
and suggested data domains from shared key attributes.
"""

import csv
import json
import os


def _base_name(name):
    return name.split(".")[-1]


def _tables_from_lineage(path):
    with open(path) as f:
        edges = json.load(f)
    tables = set()
    for edge in edges:
        for side in ("source", "target"):
            parent = edge[side]["parent"]
            if parent["type"] == "Table":
                tables.add(_base_name(parent["name"]))
    return tables


def _attributes_from_csv(path):
    with open(path, newline="") as f:
        header = next(csv.reader(f), [])
    return {col.strip().lower() for col in header if col.strip()}


def compute_attribute_overlap(sas_lineage_path, sf_lineage_path, data_dir="sample_data"):
    platform_tables = {
        "SAS": _tables_from_lineage(sas_lineage_path),
        "Snowflake": _tables_from_lineage(sf_lineage_path),
    }
    all_tables = sorted(platform_tables["SAS"] | platform_tables["Snowflake"])

    table_attrs = {}
    for table in all_tables:
        csv_path = os.path.join(data_dir, table + ".csv")
        if os.path.isfile(csv_path):
            table_attrs[table] = _attributes_from_csv(csv_path)

    tables = sorted(table_attrs)

    attr_tables = {}
    for table in tables:
        for attr in table_attrs[table]:
            attr_tables.setdefault(attr, set()).add(table)

    attributes = []
    for attr in sorted(attr_tables):
        attr_tbls = sorted(attr_tables[attr])
        platforms = sorted(
            p for p in ("SAS", "Snowflake")
            if any(t in platform_tables[p] for t in attr_tbls)
        )
        attributes.append({
            "attribute": attr,
            "tables": attr_tbls,
            "platforms": platforms,
            "table_count": len(attr_tbls),
        })

    n = len(tables)
    shared_counts = [[0] * n for _ in range(n)]
    jaccard = [[0.0] * n for _ in range(n)]
    for i, ti in enumerate(tables):
        for j, tj in enumerate(tables):
            inter = table_attrs[ti] & table_attrs[tj]
            union = table_attrs[ti] | table_attrs[tj]
            shared_counts[i][j] = len(inter)
            jaccard[i][j] = 1.0 if i == j else round(len(inter) / len(union), 4) if union else 0.0

    key_attrs = {a for a, tbls in attr_tables.items() if len(tbls) >= 2}

    parent = {t: t for t in tables}

    def find(t):
        while parent[t] != t:
            parent[t] = parent[parent[t]]
            t = parent[t]
        return t

    for attr in key_attrs:
        tbls = sorted(attr_tables[attr])
        for other in tbls[1:]:
            parent[find(other)] = find(tbls[0])

    groups = {}
    for t in tables:
        groups.setdefault(find(t), []).append(t)

    suggested_domains = []
    for members in groups.values():
        members = sorted(members)
        group_keys = sorted(
            a for a in key_attrs
            if len(attr_tables[a] & set(members)) >= 2
        )
        union_attrs = sorted(set().union(*(table_attrs[t] for t in members)))
        domain = " / ".join(group_keys) if group_keys else members[0]
        suggested_domains.append({
            "domain": domain,
            "tables": members,
            "key_attributes": group_keys,
            "attributes": union_attrs,
        })
    suggested_domains.sort(key=lambda d: d["domain"])

    return {
        "attributes": attributes,
        "overlap_matrix": {
            "tables": tables,
            "shared_counts": shared_counts,
            "jaccard": jaccard,
        },
        "suggested_domains": suggested_domains,
    }
