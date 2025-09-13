import streamlit as st
from pyvis.network import Network
import tempfile
import streamlit.components.v1 as components

st.title("📊 Parallel Run Lineage Graph Demo")

# -------------------------------
# SAS Lineage
# -------------------------------
sas_edges = [
    ("CUSTOMER.sas7bdat", "JOB01_LOAD_CUSTOMER"),
    ("JOB01_LOAD_CUSTOMER", "WORK.CUSTOMER"),
    ("TRANSACTIONS.sas7bdat", "JOB02_LOAD_TRANSACTIONS"),
    ("JOB02_LOAD_TRANSACTIONS", "WORK.TRANSACTIONS"),
    ("WORK.CUSTOMER", "JOB03_CALC_AVG_BAL"),
    ("WORK.TRANSACTIONS", "JOB03_CALC_AVG_BAL"),
    ("JOB03_CALC_AVG_BAL", "WORK.AVG_MONTHLY_BAL")
]

sas_net = Network(height="400px", width="100%", notebook=False, directed=True)

# Add nodes and edges
for edge in sas_edges:
    sas_net.add_node(edge[0], label=edge[0], color="skyblue" if "JOB" not in edge[0] else "orange")
    sas_net.add_node(edge[1], label=edge[1], color="skyblue" if "JOB" not in edge[1] else "orange")
    sas_net.add_edge(edge[0], edge[1])

# Write HTML to temporary file
sas_file = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
sas_net.write_html(sas_file.name, open_browser=False)

st.subheader("🔵 SAS Lineage")
components.html(open(sas_file.name, 'r', encoding='utf-8').read(), height=450)

# -------------------------------
# Snowflake Lineage
# -------------------------------
sf_edges = [
    ("customer.csv", "LOAD_CUSTOMER"),
    ("LOAD_CUSTOMER", "STG_CUSTOMER"),
    ("transactions.csv", "LOAD_TRANSACTIONS"),
    ("LOAD_TRANSACTIONS", "STG_TRANSACTIONS"),
    ("STG_CUSTOMER", "TRF_AVG_BAL"),
    ("STG_TRANSACTIONS", "TRF_AVG_BAL"),
    ("TRF_AVG_BAL", "CUSTOMER_BAL")
]

sf_net = Network(height="400px", width="100%", notebook=False, directed=True)

# Add nodes and edges
for edge in sf_edges:
    sf_net.add_node(edge[0], label=edge[0], color="skyblue" if "LOAD" not in edge[0] and "TRF" not in edge[0] else "orange")
    sf_net.add_node(edge[1], label=edge[1], color="skyblue" if "LOAD" not in edge[1] and "TRF" not in edge[1] else "orange")
    sf_net.add_edge(edge[0], edge[1])

# Write HTML to temporary file
sf_file = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
sf_net.write_html(sf_file.name, open_browser=False)

st.subheader("🟠 Snowflake Lineage")
components.html(open(sf_file.name, 'r', encoding='utf-8').read(), height=450)

# -------------------------------
# Validation Report
# -------------------------------
st.markdown("---")
st.subheader("📝 Validation Report")
st.write("""
Row count mismatch detected in `avg_monthly_balance` / `CUSTOMER_BAL`.  

- **SAS JOB03_CALC_AVG_BAL**: includes all customers (3 rows).  
- **Snowflake TRF_AVG_BAL**: excludes inactive customers (2 rows).  
- **Root Cause**: Different filter conditions applied in the final aggregation step.
""")
