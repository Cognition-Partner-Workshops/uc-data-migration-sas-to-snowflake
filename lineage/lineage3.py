import os
import json
import tempfile
import networkx as nx
import streamlit as st
from pyvis.network import Network
import streamlit.components.v1 as components

# --------------------------------
# Function: Build and return graph
# --------------------------------
import pickle
import tempfile
from pyvis.network import Network

def add_lineage_to_pyvis(net, G, start_table, direction="upstream", error_tables=None):
    if error_tables is None:
        error_tables = set()

    existing_nodes = set(net.nodes)  # keep track to avoid duplicates

    # Decide traversal function
    if direction == "upstream":
        neighbors = lambda n: G.predecessors(n)
    else:
        neighbors = lambda n: G.successors(n)

    visited = set()

    def dfs(node):
        if node in visited:
            return
        visited.add(node)

        for nbr in neighbors(node):
            # Our graph has table→job→table pattern
            # So get the triple around this edge if it fits
            if G.nodes[nbr].get("type") == "job":  # job node between tables
                job = nbr

                if direction == "upstream":
                    # find predecessor tables of job
                    for src_table in G.predecessors(job):
                        add_triplet_to_pyvis(src_table, job, node, net, existing_nodes, error_tables)
                        dfs(src_table)
                else:
                    # find successor tables of job
                    for trg_table in G.successors(job):
                        add_triplet_to_pyvis(node, job, trg_table, net, existing_nodes, error_tables)
                        dfs(trg_table)

    dfs(start_table)

    # Save to temp file
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
    net.write_html(tmp.name, open_browser=False)
    return tmp.name

def add_triplet_to_pyvis(src_table, job, trg_table, net, existing_nodes, error_tables):
    # Add nodes with colors
    nodes = [
        (src_table, "skyblue"),
        (trg_table, "lightgreen"),
        (job, "orange")
    ]
    for n, default_color in nodes:
        color = "red" if n in error_tables else default_color
        if n not in existing_nodes:
            net.add_node(
                n,
                label=n,
                color=color,
                shape="dot",
                font={"vadjust": -20}
            )
            existing_nodes.add(n)

    # Add edges
    net.add_edge(src_table, job, color="red" if src_table in error_tables or job in error_tables else "gray")
    net.add_edge(job, trg_table, color="red" if trg_table in error_tables or job in error_tables else "gray")

def load_lineage_graph(LINEAGE_FILE) -> nx.DiGraph:
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

# --------------------------------
# Streamlit UI
# --------------------------------
#st.set_page_config(layout="wide")
#LINEAGE_FILE = "SAS_lineage_graph.pkl"

st.title("📊 SAS vs Snowflake Lineage")

sas_col, sf_col = st.columns(2)
error_tables = ["WORK.MONTHLY_AMB", "table_Y"]
with sas_col:
    st.subheader("🔵 SAS Lineage")
    G = load_lineage_graph("SAS_lineage_graph.pkl")
    net = Network(height="300px", width="100%", notebook=False, directed=True)
    sas_html = add_lineage_to_pyvis(net, G, start_table="WORK.MONTHLY_AMB", direction="upstream")
    #sas_html = build_lineage_graph(sas_lineage, "SAS Lineage")
    #build_lineage_graph(lineage_pickle_path: str, target_table: str, html_output: str = "lineage.html"):
    components.html(open(sas_html, 'r', encoding='utf-8').read(), height=300)

with sf_col:
    st.subheader("🟠 Snowflake Lineage")
    G = load_lineage_graph("SF_lineage_graph.pkl")
    net = Network(height="300px", width="100%", notebook=False, directed=True)
    sf_html = add_lineage_to_pyvis(net, G, start_table="WORK.MONTHLY_AMB", direction="upstream", error_tables=error_tables)
    #sf_html = build_lineage_graph(snowflake_lineage, "Snowflake Lineage")
    #sf_html = build_lineage_graph(snowflake_lineage, error_tables)
    #st.components.v1.html(open(sf_html).read(), height=500, scrolling=True)
    components.html(open(sf_html, 'r', encoding='utf-8').read(), height=300)

