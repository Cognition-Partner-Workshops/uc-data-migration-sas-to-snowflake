# lineage_search.py
import pickle
import networkx as nx
from typing import List, Dict

def get_upstream_chains(graph: nx.DiGraph, target_table: str) -> List[List[Dict]]:
    """All paths ending in target_table"""
    if target_table not in graph:
            print(f"Start {target_table} not found in graph.")
            return []

    chains = []
    for src in graph.nodes():
        if nx.has_path(graph, src, target_table):
            for path in nx.all_simple_paths(graph, src, target_table):
                steps = []
                for i in range(len(path)-1):
                    e = graph.get_edge_data(path[i], path[i+1])
                    steps.append({"from": path[i], "job": e["job"], "code": e["code"], "to": path[i+1]})
                chains.append(steps)
    return chains

def get_downstream_chains(graph: nx.DiGraph, start_table: str) -> List[List[Dict]]:
    """All paths starting from start_table"""
    if start_table not in graph:
            print(f"Start {start_table} not found in graph.")
            return []

    chains = []
    for trg in graph.nodes():
        if nx.has_path(graph, start_table, trg):
            for path in nx.all_simple_paths(graph, start_table, trg):
                steps = []
                for i in range(len(path)-1):
                    e = graph.get_edge_data(path[i], path[i+1])
                    steps.append({"from": path[i], "job": e["job"], "code": e["code"], "to": path[i+1]})
                chains.append(steps)
    return chains

# Example usage
import os
import pickle
import networkx as nx

LINEAGE_FILE = "SAS_lineage_graph.pkl"

def load_lineage_graph() -> nx.DiGraph:
    if not os.path.exists(LINEAGE_FILE) or os.path.getsize(LINEAGE_FILE) == 0:
        raise FileNotFoundError(
            f"❌ Lineage graph file '{LINEAGE_FILE}' does not exist or is empty. "
            "Run build_lineage_graph.py first to generate it."
        )

    with open(LINEAGE_FILE, "rb") as f:
        graph = pickle.load(f)

    if not isinstance(graph, nx.DiGraph) or len(graph.nodes) == 0:
        raise ValueError("❌ The lineage graph file is invalid or has no nodes.")
    
    print(f"✅ Loaded lineage graph with {len(graph.nodes)} nodes and {len(graph.edges)} edges.")
    return graph


# --- Usage ---
if __name__ == "__main__":
    table = "WORK.DAILY_BALANCE"
    try:
        G = load_lineage_graph()
    except Exception as e:
        print(e)

    print("\n🔼 Upstream:")
    for chain in get_upstream_chains(G, table):
        if not chain:
            continue  # skip empty paths
        print(" → ".join([step['from'] for step in chain] + [chain[-1]['to']]))

    print("\n🔽 Downstream:")
    for chain in get_downstream_chains(G, table):
        if not chain:
            continue  # skip empty paths
        print(" → ".join([step['from'] for step in chain] + [chain[-1]['to']]))
