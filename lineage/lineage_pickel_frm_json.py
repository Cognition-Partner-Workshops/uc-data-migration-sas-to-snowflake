#--------------------------------------------------------------------------------
# This function wqill parse Collibra lineage JSON, build the full Ddirected Graph (DAG),
# and persist it to disk as a XX_lineage_graph.pkl file. 
# This only needs to run when Collibra metadata is refreshed (e.g. weekly).
#--------------------------------------------------------------------------------
import json
import networkx as nx
import pickle

def build_DAG(lineage_data):
    # Build the directed graph
    G = nx.DiGraph()

    for item in lineage_data:
        src_table = item["source"]["parent"]["name"]
        trg_table = item["target"]["parent"]["name"]
        job = item["transformations"]["transformation_display_name"]
        code = item["transformations"]["path"]
        #highlights = item["transformations"]["highlights"]["text"]
        highlights = [item.get("text", "") for item in item["transformations"]["highlights"]]

        # Skip if missing tables
        if not src_table or not trg_table:
            continue

        # Add nodes
        G.add_node(src_table, type="table")
        G.add_node(trg_table, type="table")

        # Add job node
        G.add_node(job, type="job", code=code, highlights=highlights)

        # Add edges source -> job -> target
        # Connect them: table → job → table
        G.add_edge(src_table, job)
        G.add_edge(job, trg_table)

    return G

# --- Usage ---
if __name__ == "__main__":
    # Load the raw lineage JSON (Collibra) for SAS
    with open("SAS_lineage.json", "r") as f:
        SAS_lineage_data = json.load(f)
    
    try:
        G = build_DAG(SAS_lineage_data)
        # Persist to pickle
        with open("SAS_lineage_graph.pkl", "wb") as f:
            pickle.dump(G, f)
        print("✅ Lineage graph saved for SAS to SAS_lineage_graph.pkl")
    except Exception as e:
        print(e)
        
    # Load the raw lineage JSON (Collibra) for Snowflake (SF)
    with open("SF_lineage.json", "r") as f:
        SF_lineage_data = json.load(f)
    
    try:
        G = build_DAG(SF_lineage_data)
        # Persist to pickle
        with open("SF_lineage_graph.pkl", "wb") as f:
            pickle.dump(G, f)
        print("✅ Lineage graph saved for Snowflake to SF_lineage_graph.pkl")
    except Exception as e:
        print(e)
        
