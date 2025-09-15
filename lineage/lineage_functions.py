import os
import pickle
import tempfile
import networkx as nx
import streamlit as st
from pyvis.network import Network
import streamlit.components.v1 as components

# --------------------------------
# Function: Build and return graph
# --------------------------------

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

    ## Save to temp file
    #tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
    #net.write_html(tmp.name, open_browser=False)
    #return tmp.name
    
    # Save to HTML string (without writing to file)
    html_string = net.generate_html()
    # Force transparent background in the body
    #html_string = html_string.replace("background-color: #ffffff;", "background-color: transparent;")
    return html_string

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
# Function: Locate and Color the nodes
# Current Node: White ring
# Error Node & Edges: Red
# --------------------------------
def mark_current_and_error_nodes(net, G, current_table, error_tables=None, add_legend=True):
    if error_tables is None:
        error_tables = set()

    existing_nodes = set()
    visited = set()
    added_edges = set()  # Track edges to avoid duplicates

    def dfs(node):
        if node in visited:
            return
        visited.add(node)

        # Determine node color
        # Determine node color
        if node in error_tables and node != current_table:
            node_color = {"background": "red", "border": "red", "highlight": "red"}
            border_width = 1
        elif node in error_tables and node == current_table:
            node_color = {"background": "red", "border": "white", "highlight": "red"}
            border_width = 3
        elif node == current_table:
            node_color = {"background": "skyblue", "border": "white", "highlight": "skyblue"}
            border_width = 3
        elif G.nodes[node].get("type") == "job":
            node_color = {"background": "orange", "border": "orange", "highlight": "orange"}
            border_width = 1
        else:
            node_color = {"background": "skyblue", "border": "skyblue", "highlight": "skyblue"}
            border_width = 1

        if node not in existing_nodes:
            net.add_node(
                node,
                label=node,
                color=node_color,
                borderWidth=border_width,
                title=node,
                shape="dot",
                font={"vadjust": -20}
            )
            existing_nodes.add(node)

        # Traverse neighbors (predecessors and successors)
        for nbr in set(G.predecessors(node)).union(set(G.successors(node))):
            dfs(nbr)

            # Track edges to avoid duplicates
            edge_tuple = (node, nbr)
            if edge_tuple not in added_edges:
                edge_color = "red" if node in error_tables or nbr in error_tables else "white"
                net.add_edge(node, nbr, color=edge_color)
                added_edges.add(edge_tuple)

    dfs(current_table)
    return net.generate_html()

#Function to add legend to the container
def build_lineage_legend(net):
    """
    Create a horizontal, centered legend for lineage graphs.
    Returns the HTML string for embedding in Streamlit.
    """
    net.options = {
                    "configure": {"enabled": False},
                    "interaction": {
                        "dragNodes": False,
                        "zoomView": False, 
                        "dragView": False
                    },
                    "physics": {"enabled": False},
                    }
    # Legend items: label + color
    legend_items = [
        ("Current Table", {"background": "skyblue", "border": "white", "highlight": "skyblue"}),
        ("Error Table", {"background": "red", "border": "red", "highlight": "red"}),
        ("Job Node", {"background": "orange", "border": "orange", "highlight": "orange"}),
    ]

    # Horizontal layout
    spacing = 100
    start_x = -((len(legend_items) - 1) * spacing) // 2  # center align
    y = 0

    for i, (label, color) in enumerate(legend_items):
        net.add_node(
            f"legend_{i}",
            label=label,
            color=color,
            x=start_x + i * spacing,
            y=y,
            fixed=True,
            physics=False,
            shape="dot",
            font={"size": 15, "vadjust": -30}
        )

    return net.generate_html()

import networkx as nx

#Function to identify Sub-Graph for a given node/table within a pickle networks
def get_node_network(G, current_table):
    # Example usage
    #current_table = "my_table"  # Replace with your input
    #subgraph = get_node_network(G, current_table)
    
    if current_table not in G:
        #raise ValueError(f"Node '{current_table}' not found in the graph.")
        return None

    # Find the connected component that contains the node
    for component in nx.connected_components(G):
        if current_table in component:
            # Extract subgraph
            return G.subgraph(component).copy()
        
    return None
