import streamlit as st
from pyvis.network import Network
import tempfile, json
import streamlit.components.v1 as components

# -----------------------------
# Collibra-style lineage JSONs
# -----------------------------
sas_lineage = [
  {
    "src": {
      "nodes": [
        { "name": "sas", "type": "System" },
        { "name": "Finance_DB", "type": "Database" },
        { "name": "RAW", "type": "Schema" }
      ],
      "parent": { "name": "CUST_ACCOUNTS.sas7bdat", "type": "Table" }
    },
    "trg": {
      "nodes": [
        { "name": "sas", "type": "System" },
        { "name": "Finance_DB", "type": "Database" },
        { "name": "STAGING", "type": "Schema" }
      ],
      "parent": { "name": "WORK.CUST_ACCOUNTS", "type": "Table" }
    },
    "source_code": {
      "path": "source_codes/JOB01_LOAD_CUST_ACCOUNTS.sas",
      "highlights": [{ "start": 0, "len": 120 }],
      "transformation_display_name": "JOB01_LOAD_CUST_ACCOUNTS"
    }
  },
  {
    "src": {
      "nodes": [
        { "name": "sas", "type": "System" },
        { "name": "Finance_DB", "type": "Database" },
        { "name": "RAW", "type": "Schema" }
      ],
      "parent": { "name": "DAILY_BALANCE.sas7bdat", "type": "Table" }
    },
    "trg": {
      "nodes": [
        { "name": "sas", "type": "System" },
        { "name": "Finance_DB", "type": "Database" },
        { "name": "STAGING", "type": "Schema" }
      ],
      "parent": { "name": "WORK.DAILY_BALANCE", "type": "Table" }
    },
    "source_code": {
      "path": "source_codes/JOB02_LOAD_DAILY_BALANCE.sas",
      "highlights": [{ "start": 0, "len": 120 }],
      "transformation_display_name": "JOB02_LOAD_DAILY_BALANCE"
    }
  },
  {
    "src": {
      "nodes": [
        { "name": "sas", "type": "System" },
        { "name": "Finance_DB", "type": "Database" },
        { "name": "STAGING", "type": "Schema" }
      ],
      "parent": { "name": "WORK.CUST_ACCOUNTS", "type": "Table" }
    },
    "trg": {
      "nodes": [
        { "name": "sas", "type": "System" },
        { "name": "Finance_DB", "type": "Database" },
        { "name": "STAGING", "type": "Schema" }
      ],
      "parent": { "name": "WORK.MONTHLY_AMB", "type": "Table" }
    },
    "source_code": {
      "path": "source_codes/JOB03_CALC_AMB.sas",
      "highlights": [{ "start": 0, "len": 580 }],
      "transformation_display_name": "JOB03_CALC_AMB"
    }
  },
  {
    "src": {
      "nodes": [
        { "name": "sas", "type": "System" },
        { "name": "Finance_DB", "type": "Database" },
        { "name": "STAGING", "type": "Schema" }
      ],
      "parent": { "name": "WORK.DAILY_BALANCE", "type": "Table" }
    },
    "trg": {
      "nodes": [
        { "name": "sas", "type": "System" },
        { "name": "Finance_DB", "type": "Database" },
        { "name": "STAGING", "type": "Schema" }
      ],
      "parent": { "name": "WORK.MONTHLY_AMB", "type": "Table" }
    },
    "source_code": {
      "path": "source_codes/JOB03_CALC_AMB.sas",
      "highlights": [{ "start": 0, "len": 580 }],
      "transformation_display_name": "JOB03_CALC_AMB"
    }
  }
]

snowflake_lineage = [
  {
    "src": {
      "nodes": [
        { "name": "snowflake", "type": "System" },
        { "name": "FINANCE_DB", "type": "Database" },
        { "name": "RAW", "type": "Schema" }
      ],
      "parent": { "name": "CUST_ACCOUNTS", "type": "Table" }
    },
    "trg": {
      "nodes": [
        { "name": "snowflake", "type": "System" },
        { "name": "FINANCE_DB", "type": "Database" },
        { "name": "STAGING", "type": "Schema" }
      ],
      "parent": { "name": "STG.CUST_ACCOUNTS", "type": "Table" }
    },
    "source_code": {
      "path": "source_codes/JOB01_LOAD_CUST_ACCOUNTS.sql",
      "highlights": [{ "start": 0, "len": 120 }],
      "transformation_display_name": "JOB01_LOAD_CUST_ACCOUNTS"
    }
  },
  {
    "src": {
      "nodes": [
        { "name": "snowflake", "type": "System" },
        { "name": "FINANCE_DB", "type": "Database" },
        { "name": "RAW", "type": "Schema" }
      ],
      "parent": { "name": "DAILY_BALANCE", "type": "Table" }
    },
    "trg": {
      "nodes": [
        { "name": "snowflake", "type": "System" },
        { "name": "FINANCE_DB", "type": "Database" },
        { "name": "STAGING", "type": "Schema" }
      ],
      "parent": { "name": "STG.DAILY_BALANCE", "type": "Table" }
    },
    "source_code": {
      "path": "source_codes/JOB02_LOAD_DAILY_BALANCE.sql",
      "highlights": [{ "start": 0, "len": 120 }],
      "transformation_display_name": "JOB02_LOAD_DAILY_BALANCE"
    }
  },
  {
    "src": {
      "nodes": [
        { "name": "snowflake", "type": "System" },
        { "name": "FINANCE_DB", "type": "Database" },
        { "name": "STAGING", "type": "Schema" }
      ],
      "parent": { "name": "STG.CUST_ACCOUNTS", "type": "Table" }
    },
    "trg": {
      "nodes": [
        { "name": "snowflake", "type": "System" },
        { "name": "FINANCE_DB", "type": "Database" },
        { "name": "STAGING", "type": "Schema" }
      ],
      "parent": { "name": "MONTHLY_AMB", "type": "Table" }
    },
    "source_code": {
      "path": "source_codes/JOB03_CALC_AMB.sql",
      "highlights": [{ "start": 0, "len": 85, "text": "WHERE c.is_active = 'ACTIVE'" }],
      "transformation_display_name": "JOB03_CALC_AMB"
    }
  },
{
    "src": {
      "nodes": [
        { "name": "snowflake", "type": "System" },
        { "name": "FINANCE_DB", "type": "Database" },
        { "name": "STAGING", "type": "Schema" }
      ],
      "parent": { "name": "STG.DAILY_BALANCE", "type": "Table" }
    },
    "trg": {
      "nodes": [
        { "name": "snowflake", "type": "System" },
        { "name": "FINANCE_DB", "type": "Database" },
        { "name": "STAGING", "type": "Schema" }
      ],
      "parent": { "name": "MONTHLY_AMB", "type": "Table" }
    },
    "source_code": {
      "path": "source_codes/JOB03_CALC_AMB.sql",
      "highlights": [{ "start": 0, "len": 85, "text": "WHERE c.is_active = 'ACTIVE'" }],
      "transformation_display_name": "JOB03_CALC_AMB"
    }
  }
]

# --------------------------------
# Function: Build and return graph
# --------------------------------
def build_lineage_graph(lineage_json, error_tables=None):
    """
    Builds a PyVis graph from lineage JSON.
    Highlights nodes/edges in red if they are in error_tables.
    
    Parameters:
    - lineage_json: list of lineage links
    - error_tables: set or list of table names that should be highlighted in red
    """
    if error_tables is None:
        error_tables = set()
    else:
        error_tables = set(error_tables)

    net = Network(height="300px", width="100%", notebook=False, directed=True)
    #net.barnes_hut()
    #net.barnes_hut(gravity=-20000, central_gravity=0.3, spring_length=200, spring_strength=0.01, damping=0.09)
    #for hierarchical direction: UD = top-down layout and LR=left to right layout:
    #net.set_options("""
    #var options = {
    #"layout": {
    #    "hierarchical": {
    #    "enabled": true,
    #    "levelSeparation": 150,
    #    "nodeSpacing": 150,
    #    "treeSpacing": 200,
    #    "direction": "LR", 
    #    "sortMethod": "directed"
    #    }
    #},
    #"physics": {"enabled": false}
    #}
    #""")

    existing_nodes = set()  # keep track of nodes already added
    for link in lineage_json:
        src_table = link["src"]["parent"]["name"]
        trg_table = link["trg"]["parent"]["name"]
        job = link["source_code"]["transformation_display_name"]

        # Determine node colors
        nodes = [
            (src_table, "skyblue"),
            (trg_table, "lightgreen"),
            (job, "orange")
        ]
        # Add nodes if they don't exist
        for n, default_color in nodes:
            color = "red" if n in error_tables else default_color
            if n not in existing_nodes:
                net.add_node(
                    n,
                    label=n,
                    color=color,
                    shape="dot" if n == job else "dot",
                    font={"vadjust": -20}  # label above the node
                )
                existing_nodes.add(n)

        # Add edges; color red if either end is an error table
        src_edge_color = "red" if src_table in error_tables or job in error_tables else nodes[0]
        trg_edge_color = "red" if trg_table in error_tables or job in error_tables else nodes[1]

        net.add_edge(src_table, job)
        net.add_edge(job, trg_table, color=trg_edge_color)

    # Save to temp file
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
    net.write_html(tmp.name, open_browser=False)
    return tmp.name

# --------------------------------
# Streamlit UI
# --------------------------------
#st.set_page_config(layout="wide")
st.title("📊 SAS vs Snowflake Lineage")

sas_col, sf_col = st.columns(2)
error_tables = ["MONTHLY_AMB", "table_Y"]
with sas_col:
    st.subheader("🔵 SAS Lineage")
    sas_html = build_lineage_graph(sas_lineage, "SAS Lineage")
    components.html(open(sas_html, 'r', encoding='utf-8').read(), height=300)

with sf_col:
    st.subheader("🟠 Snowflake Lineage")
    #sf_html = build_lineage_graph(snowflake_lineage, "Snowflake Lineage")
    sf_html = build_lineage_graph(snowflake_lineage, error_tables)
    #st.components.v1.html(open(sf_html).read(), height=500, scrolling=True)
    components.html(open(sf_html, 'r', encoding='utf-8').read(), height=300)

