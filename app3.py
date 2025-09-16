import io
import os
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components


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
# Common Functions
# ------------------------------------------------------------------------
#from lineage.lineage_functions import add_lineage_to_pyvis, load_lineage_graph, mark_current_and_error_nodes, build_lineage_legend
from lineage.lineage_functions import generate_lineage_graph, collect_upstream_tables
from helper_functions import decode_value, suggest_columns_for_rule, validate_datasets

sas_lineage_pickle = "./lineage/SAS_lineage_graph.pkl"
sf_lineage_pickle = "./lineage/SF_lineage_graph.pkl"

# ------------------------------------------------------------------------
# Session State Initialization
# ------------------------------------------------------------------------
if "validations_list" not in st.session_state:
    st.session_state.validations_list = []

if "validation_results" not in st.session_state:
    st.session_state["validation_results"] = None

if "any_failures" not in st.session_state:
    st.session_state["any_failures"] = None

if "SAS_directory" not in st.session_state:
    st.session_state["SAS_directory"] = "./sample_data"
if "SF_directory" not in st.session_state:
    st.session_state["SF_directory"] = "./sample_data"

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
        sas_table_name = os.path.splitext(sas_file.name)[0]  # remove extension

        st.success(f"SAS dataset loaded: {sas_df.shape[0]} rows, {sas_df.shape[1]} cols")
    except Exception as e:
        st.error(f"❌ Error reading SAS dataset: {e}")

if sf_file:
    try:
        sf_df = pd.read_csv(io.StringIO(sf_file.getvalue().decode("utf-8")))

        sf_table_name = os.path.splitext(sf_file.name)[0]  # remove extension

        st.success(f"Snowflake CSV loaded: {sf_df.shape[0]} rows, {sf_df.shape[1]} cols")
    except Exception as e:
        st.error(f"❌ Error reading Snowflake CSV: {e}")


# ---------------------------
# Preview Selected Datasets
# ---------------------------
if sas_df is not None and sf_df is not None:
    st.subheader("📊 Preview Uploaded Datasets")
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
            if st.button("❌ Delete Selected Validations"):
                st.session_state.validations_list = [
                item for i, item in enumerate(st.session_state.validations_list) if i not in selected_validations
                ]
                st.rerun()


    st.markdown("---")
# ---------------------------
# Run Validations
# ---------------------------
    if st.button("🧪 Run All Validations"):
        #if "validations_list" not in st.session_state or not st.session_state.validations_list:
        if not st.session_state.get("validations_list"):
            st.error("No validations selected. Select validations to apply them on selected datasets.")
            st.stop()

        st.session_state["any_failures"], st.session_state["validation_results"]  = validate_datasets(
            st.session_state.validations_list, 
            sas_df,
            sf_df
            )
        #Reset the lineage for each change in validations (button clicked)
        #del st.session_state["sas_lineage"]
        st.session_state.pop("sas_lineage", None)
        #del st.session_state["sf_lineage"]
        st.session_state.pop("sf_lineage", None)

# ---------------------------
# Show Validations Results
# ---------------------------
    if st.session_state["validation_results"] is not None:
        st.subheader("✅ Validation Results")
        st.dataframe(pd.DataFrame(st.session_state["validation_results"]), hide_index=True)

        if st.session_state["any_failures"]:
            st.error(f"❌ Reconcillation Failures Observed for Table: {sf_table_name}")
            st.session_state.error_tables = []
            st.session_state.error_tables.append(sf_table_name)
            st.markdown("---")
        else:
            st.success(f"✔️ No Reconcillation Failures Observed for Table: {sf_table_name}")
            st.markdown("---")
            st.stop()

# ---------------------------
# Show Lineage
# ---------------------------
        st.subheader("🔗Lineage")
        sas_col, sf_col = st.columns(2)

        with sas_col:
            #table = "TABLE2"
            net_key = "sas_lineage"
            if net_key not in st.session_state:
                #error_tables = ["table_X", "table_Y"]
    
                sas_html = generate_lineage_graph(sas_lineage_pickle, sas_table_name, None)
                st.session_state[net_key] = sas_html
            else:
                sas_html = st.session_state[net_key]

            with st.container(border=True):
                st.markdown("#### 🟠 SAS Lineage")
                components.html(sas_html, height=300)

        with sf_col:
            net_key = "sf_lineage"
            if net_key not in st.session_state:
                #table="MONTHLY_AMB"
                #error_tables = ["MONTHLY_AMB", "table_Y"]

                sf_html = generate_lineage_graph(sf_lineage_pickle, sf_table_name, st.session_state.error_tables)
                st.session_state[net_key] = sf_html
            else:
                sf_html = st.session_state[net_key]

            with st.container(border=True):
                st.markdown("#### 🔵 Snowflake Lineage")
                components.html(sf_html, height=300)

                ##Add Legend
                #net = Network(height="30px", width="100%", 
                #            bgcolor="#222222", font_color="white", 
                #            notebook=False, directed=False
                #            )
                #legend_html = build_lineage_legend(net)  # Use the separate legend function
                #st.components.v1.html(clean_pyvis_html(legend_html, False), height=35, scrolling=False)

# ---------------------------
# Repeat validations for the upstream dataset until clean
# ---------------------------
        #any_failures = True
        if st.session_state["any_failures"]:
            if st.button("⬆️🔗❓ Check Upstream Dependencies?"):
                st.info("Checkin Upstream Dependencies")
                #1. Get the current failed table as a starting point
                #2. Navigate the lineage upstream and identify upstream tables
                #3. Perform the same validations on these tables
                #4. These tables may not have all the columns as the current table - to be handled

                st.session_state.upstream_tables = []

                new_val = {"table": sf_table_name, "rank": 0}
                st.session_state.upstream_tables.append(new_val)

                # Collect recursively
                st.session_state.upstream_tables.extend(
                    collect_upstream_tables(sf_lineage_pickle, sf_table_name)
                )

                st.write(st.session_state["SF_directory"])
                st.write(st.session_state.upstream_tables)
# ---------------------------
# Test Report: Summarize all failures as 
# ---------------------------
