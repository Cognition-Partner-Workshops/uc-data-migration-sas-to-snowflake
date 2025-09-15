import io
import os
import pandas as pd
import streamlit as st
import networkx as nx
from pyvis.network import Network
import streamlit.components.v1 as components

from lineage.lineage_functions import add_lineage_to_pyvis, load_lineage_graph
from helper_functions import decode_value, suggest_columns_for_rule, validate_datasets, clean_pyvis_html

##########################################################################
# Functionality
# 1. Load the starting point - Tables to check for post-migration validation
# 2. Get the validation rules
# 3. Generate code for validations using LLM
# 2. Apply the validation rules on datasets and check if there are any reconcillation issues
# 3. If issues found check the other tables in the up-stream lineage for issues
# 4. Collect all tables with issues and retrieve the transformation logic
# 5. Provide the details to LLM pompt and generate a summary
##########################################################################
#Execution
#streamlit run app3.py

# ------------------------------------------------------------------------
# Session State Initialization
# ------------------------------------------------------------------------
if "validations_list" not in st.session_state:
    st.session_state.validations_list = []

# ------------------------------------------------------------------------
# Streamlit UI
# ------------------------------------------------------------------------
st.set_page_config(page_title="Agentic RAG Migration Validation", layout="wide")
#Custom CSS for Sub Headers
st.markdown("""
    <style>
    h1 { font-size: 24px !important; color: white; }
    h2 { font-size: 20px !important; color: #4CAF50; }
    h3 { font-size: 18px !important; color: #ff5733; }
    h4 { font-size: 16px !important; color: #1f77b4; }
    
    .stMarkdown, p { font-size: 14px !important; }
    </style>
""", unsafe_allow_html=True)

st.title("Agentic RAG Migration Validation & Testing (SAS → Snowflake Demo)")

# Reset button in sidebar
if st.sidebar.button("🔄 Reset App"):
    # Clear all session state variables
    for key in list(st.session_state.keys()):  # convert to list to avoid runtime error
        del st.session_state[key]
    
    # Rerun the app
    #st.experimental_rerun()  # use experimental_rerun() in newer Streamlit versions
    #st.rerun()
    st.rerun(scope="app")

# Upload files
st.sidebar.header("Upload Data")
#sas_file = st.sidebar.file_uploader("Upload SAS dataset (.sas7bdat or .xpt)", type=["sas7bdat", "xpt"])
# File uploader with a fixed key
sas_file = st.sidebar.file_uploader(
    "Upload SAS dataset (.sas7bdat or .xpt)",
    type=["sas7bdat", "xpt"],
    key="sas_file"  # fixed key to track uploader
)

#sf_file = st.sidebar.file_uploader("Upload Snowflake migrated data (CSV)", type=["csv"])
# File uploader with a fixed key
sf_file = st.sidebar.file_uploader(
    "Upload Snowflake migrated data (*.csv)",
    type=["csv"],
    key="sf_file"  # fixed key to track uploader
)
sas_df = None
sf_df = None

if sas_file:
    try:
        # Pandas will auto-detect format
        sas_df = pd.read_sas(sas_file, format="sas7bdat")

        #Properly decode SAS character variables
        sas_df = sas_df.map(decode_value)
        sas_df.columns = sas_df.columns.str.lower()
        sas_table_name = os.path.splitext(sas_file.name)[0].lower()  # remove extension

        st.success(f"SAS dataset loaded: {sas_df.shape[0]} rows, {sas_df.shape[1]} cols")
    except Exception as e:
        st.error(f"❌ Error reading SAS dataset: {e}")

if sf_file:
    try:
        sf_df = pd.read_csv(io.StringIO(sf_file.getvalue().decode("utf-8")))
        sf_table_name = os.path.splitext(sf_file.name)[0].lower()  # remove extension
        st.success(f"Snowflake CSV loaded: {sf_df.shape[0]} rows, {sf_df.shape[1]} cols")
    except Exception as e:
        st.error(f"❌ Error reading Snowflake CSV: {e}")


# ---------------------------
# Preview Selected Datasets
# ---------------------------
if sas_df is not None and sf_df is not None:
    st.subheader("📊 Preview Uploaded Datasets")
    #st.write(f"**SAS Baseline : {sas_table_name} **")
    #st.dataframe(sas_df.head())
    #st.write(f"**Snowflake Data: {sf_table_name} **")
    #st.dataframe(sf_df.head())
    tab1, tab2 = st.tabs(["Preview SAS Dataset", "Preview Snowflake Dataset"])
    with tab1:
        st.header(f"**SAS Baseline : {sas_table_name} **")
        #st.dataframe(sas_df.head(), hide_index=True)
        st.table(sas_df.head())
        #st.image("https://static.streamlit.io/examples/cat.jpg", width=200)
    with tab2:
        st.header(f"**Snowflake Data: {sf_table_name} **")
        #st.dataframe(sf_df.head(), hide_index=True)
        st.table(sf_df.head())

# ---------------------------
# Validation Selection - Configuration
# ---------------------------
    st.markdown("---")
    st.subheader("⚙️ Configure Validations")

    col1, col2 = st.columns(2)

    with col1:
        rule = st.selectbox(
            "Select Validation Type",
            ["Row Count", "Row Hash", "Sum Amount", "Distinct Count", "Uniqueness", "Not Null"],
            key="rule_select"
        )

    selected_col = None
    hash_file = None

    with col2:
        if rule in ["Sum Amount", "Distinct Count", "Uniqueness", "Not Null"]:
            suggestions = suggest_columns_for_rule(rule.lower().replace(" ", "_"), sas_df)
            selected_col = st.selectbox(
                "Select Column",
                options=sas_df.columns,
                index=0 if suggestions == [] else sas_df.columns.get_loc(suggestions[0]),
                key="col_select"
            )

    if rule == "Row Hash":
        hash_file = st.file_uploader("Upload SAS Hash File", type=["csv"], key="hash_upload")

    if st.button("➕ Add Validation"):
        new_val = {
            "rule": rule,
            "column": selected_col if selected_col else "NA",
            "hash_df": pd.read_csv(hash_file) if hash_file else "NA"
        }

        st.session_state.validations_list.append(new_val)
        st.success(f"Added validation: {new_val}")

# ---------------------------
# Show Persisted List
# ---------------------------
    st.markdown("---")
    st.subheader("📋 Selected Validations")
    if len(st.session_state.validations_list) == 0:
        st.info("No validations added yet.")
    else:
        #st.table(st.session_state.validations_list)
        #df = pd.DataFrame(st.session_state.validations_list)
        event = st.dataframe(
                pd.DataFrame(st.session_state.validations_list),
                use_container_width=True,
                hide_index=True,
                on_select="rerun",
                selection_mode="multi-row",
                )
        selected_validations = event.selection.rows
        if selected_validations:
            if st.button("Delete Selected Validations"):
                st.session_state.validations_list = [
                item for i, item in enumerate(st.session_state.validations_list) if i not in selected_validations
                ]
                st.rerun()


    st.markdown("---")
# ---------------------------
# Run Validations
# ---------------------------
    if st.button("🚀 Run All Validations"):
        any_failures, results = validate_datasets(
            st.session_state.validations_list, 
            sas_df,
            sf_df
            )

# ---------------------------
# Show Validations Results
# ---------------------------
        st.subheader("✅ Validation Results")
        st.dataframe(pd.DataFrame(results), hide_index=True)
        if any_failures:
            st.error(f"❌ Reconcillation Failures Observed for Table: {sas_table_name}")
        else:
            st.success(f"✔️ No Reconcillation Failures Observed for Table: {sas_table_name}")

        st.markdown("---")
# ---------------------------
# Show Lineage
# ---------------------------
        st.subheader("🔗⬆️ Upstream Lineage")
        sas_col, sf_col = st.columns(2)
        #table="WORK.DAILY_BALANCE"
        #error_tables = ["WORK.MONTHLY_AMB", "table_Y"]

        with sas_col:
            #table="MONTHLY_AMB"
            table = sas_table_name
            error_tables = ["MONTHLY_AMB", "table_Y"]

            G = load_lineage_graph("./lineage/SAS_lineage_graph.pkl")
            #net = Network(height="300px", width="100%",
            #              bgcolor="#000000", font_color="white")
            net = Network(height="300px", width="100%", 
                          bgcolor="#222222", font_color="white", 
                          notebook=False, directed=True
                          )
            net.options = {
                "configure": {"enabled": False},
                "edges": {
                    "color": {"inherit": True},
                    "smooth": {"enabled": True, "type": "dynamic"},
                },
                "interaction": {
                    "dragNodes": True,
                    "hideEdgesOnDrag": False,
                    "hideNodesOnDrag": False,
                },
                "physics": {
                    "enabled": True,
                    "stabilization": {
                        "enabled": True,
                        "fit": True,
                        "iterations": 1000,
                        "onlyDynamicEdges": False,
                        "updateInterval": 50,
                    },
                }
            }
            # You can add this directly or modify the options dict
            sas_html = add_lineage_to_pyvis(net, G, start_table=table, direction="upstream")
            sas_html = clean_pyvis_html(sas_html, False)
            with st.container(border=True):
                st.markdown("#### 🟠 SAS Lineage")
                components.html(sas_html, height=300)

        with sf_col:
            #table="MONTHLY_AMB"
            table = sf_table_name
            error_tables = ["MONTHLY_AMB", "table_Y"]

            G = load_lineage_graph("./lineage/SF_lineage_graph.pkl")
            #net = Network(height="300px", width="100%", notebook=False, directed=True)
            net = Network(height="300px", width="100%", 
                          bgcolor="#222222", font_color="white", 
                          notebook=False, directed=True
                          )
            net.options = {
                "configure": {"enabled": False},
                "edges": {
                    "color": {"inherit": True},
                    "smooth": {"enabled": True, "type": "dynamic"},
                },
                "interaction": {
                    "dragNodes": True,
                    "hideEdgesOnDrag": False,
                    "hideNodesOnDrag": False,
                },
                "physics": {
                    "enabled": True,
                    "stabilization": {
                        "enabled": True,
                        "fit": True,
                        "iterations": 1000,
                        "onlyDynamicEdges": False,
                        "updateInterval": 50,
                    },
                }
            }

            sf_html = add_lineage_to_pyvis(net, G, start_table=table, direction="upstream", error_tables=error_tables)
            sf_html = clean_pyvis_html(sf_html, False)
            with st.container(border=True):
                st.markdown("#### 🔵 Snowflake Lineage")
                components.html(sf_html, height=300)

        # ---------------------------
        # Repeat validations for the upstream dataset until clean
        # ---------------------------

        # ---------------------------
        # Test Report: Summarize all failures as 
        # ---------------------------
