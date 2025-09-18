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
#streamlit run app4.py

# ------------------------------------------------------------------------
# Common Functions
# ------------------------------------------------------------------------
#from lineage.lineage_functions import add_lineage_to_pyvis, load_lineage_graph, mark_current_and_error_nodes, build_lineage_legend
from lineage.lineage_functions import generate_lineage_graph, collect_upstream_tables
from helper_functions import decode_value, suggest_columns_for_rule, validate_datasets
from llm_agents.llm_reports import generate_llm_summary

sas_lineage_pickle = "./lineage/SAS_lineage_graph.pkl"
sf_lineage_pickle = "./lineage/SF_lineage_graph.pkl"

SAS_directory=  "./sample_data/"
SF_directory = "./sample_data/Scenario2/"

#Function to load SAS Datasets to a Dataframe
def load_datasets(file_name: str, platform: str, qualified=True):
    if platform == "SAS":
        if not qualified:
            file_name = SAS_directory + file_name + ".sas7bdat"

        # Pandas will auto-detect format
        df = pd.read_sas(file_name, format="sas7bdat")

        #Properly decode SAS character variables
        df = df.map(decode_value)
    elif platform == "SF": 
        if not qualified:
            file_name = SF_directory + file_name + ".csv"
            df = pd.read_csv(file_name)
        else:
            df = pd.read_csv(io.StringIO(file_name.getvalue().decode("utf-8")))
    else:
        df = None

    return df

#Print lineage graph
def print_lineage_graph(platform: str, table_name: str, reproduce=False):
    if platform == "SAS":
        net_key = "sas_lineage"
        if net_key not in st.session_state or reproduce:
            #error_tables = ["table_X", "table_Y"]

            sas_html = generate_lineage_graph(sas_lineage_pickle, sas_table_name, None)
            st.session_state[net_key] = sas_html
        else:
            sas_html = st.session_state[net_key]

        with st.container(border=True):
            st.markdown("#### 🟠 SAS Lineage")
            components.html(sas_html, height=300)
    elif platform == "SF":
        net_key = "sf_lineage"
        if net_key not in st.session_state or reproduce:
            #table="MONTHLY_AMB"
            #error_tables = ["MONTHLY_AMB", "table_Y"]

            sf_html = generate_lineage_graph(sf_lineage_pickle, table_name, st.session_state.error_tables)
            st.session_state[net_key] = sf_html
        else:
            sf_html = st.session_state[net_key]

        with st.container(border=True):
            st.markdown("#### 🔵 Snowflake Lineage")
            components.html(sf_html, height=300)



# ------------------------------------------------------------------------
# Session State Initialization
# ------------------------------------------------------------------------
if "validations_list" not in st.session_state:
    st.session_state.validations_list = []

if "validation_results" not in st.session_state:
    st.session_state["validation_results"] = None

if "validation_results_run2" not in st.session_state:
    st.session_state["validation_results_run2"] = []

if "any_failures" not in st.session_state:
    st.session_state["any_failures"] = None

if "upstream_tables" not in st.session_state:
    st.session_state["upstream_tables"] = None

# ------------------------------------------------------------------------
# Streamlit UI
# ------------------------------------------------------------------------
st.set_page_config(page_title="Agentic RAG Migration Validation", layout="wide")
#Custom CSS for Sub Headers
st.markdown("""
    <style>
        h1 { font-size: 28px !important; color: white; }
        h2 { font-size: 24px !important; color: #4CAF50; }
        h3 { font-size: 18px !important; color: #ff5733; }
        h4 { font-size: 14px !important; color: #1f77b4; }
        
        .stMarkdown, a { font-size: 14px !important; }
        .stMarkdown, p { font-size: 14px !important; }
        .stMarkdown, li { font-size: 14px !important; }
        .stMarkdown, span { font-size: 14px !important; }
    </style>
""", unsafe_allow_html=True)

st.title("Agentic RAG Migration Validation & Testing")
st.header("(SAS → Snowflake Demo)")

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
        #Load SAS datasets to a DataFrame
        sas_df = load_datasets(sas_file, "SAS")
        sas_df.columns = sas_df.columns.str.lower()
        sas_table_name = os.path.splitext(sas_file.name)[0]  # remove extension

        st.success(f"SAS dataset loaded: {sas_df.shape[0]} rows, {sas_df.shape[1]} cols")
    except Exception as e:
        st.error(f"❌ Error reading SAS dataset: {e}")

if sf_file:
    try:
        sf_df = load_datasets(sf_file, "SF")
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
        st.subheader(f"**SAS Baseline : {sas_table_name} **")
        #st.dataframe(sas_df.head(), hide_index=True)
        st.table(sas_df.head())
        #st.image("https://static.streamlit.io/examples/cat.jpg", width=200)
    with tab2:
        st.subheader(f"**Snowflake Data: {sf_table_name} **")
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
            sas_table_name,
            sf_df,
            sf_table_name
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
        #Printing the validation_results first time
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
            print_lineage_graph("SAS", sas_table_name)

        with sf_col:
            print_lineage_graph("SF", sf_table_name)
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

                #Run a loop on st.session_state.upstream_tables to validate all the tables in the list
                # Filter and sort the tables, starting from rank = 1
                # print the results and the lineage at the end for all error tables
                sorted_tables = sorted(
                    [t for t in st.session_state.upstream_tables if t["rank"] >= 1],
                    key=lambda x: x["rank"]
                )

                # Make sure validation_results exists
                if "validation_results" not in st.session_state:
                    st.session_state["validation_results"] = []
                if "validation_results_run2" not in st.session_state:
                    st.session_state["validation_results_run2"] = []

                # Loop through each table in rank order
                any_failures = False
                for tbl in sorted_tables:
                    table_name = tbl["table"]
                    rank = tbl["rank"]

                    # Example: load data for each table
                    try:
                        # Try loading the data
                        sas_df = load_datasets(table_name, "SAS", False)
                        sf_df = load_datasets(table_name, "SF", False)

                    except FileNotFoundError as e:
                        st.warning(f"Skipping {table_name}: {e}")
                        continue  # move to next table

                    # Run validation
                    any_failures, new_results = validate_datasets(
                        st.session_state.validations_list, 
                        sas_df,
                        table_name,
                        sf_df,
                        table_name
                    )

                    # Append results
                    # Concatenate DataFrames instead of extend
                    st.session_state["validation_results_run2"] = pd.concat(
                        [st.session_state["validation_results"], new_results],
                        ignore_index=True
                    )
                #End For for all Tables
                #Write the validation test report
                st.subheader("✅ Validation Results for Upstream Dependencies")
                if any_failures:
                    st.dataframe(pd.DataFrame(st.session_state["validation_results_run2"]), hide_index=True)
                    st.error(f"❌ Reconcillation Failures Observed for Table: {table_name}")
                    st.session_state.error_tables.append(table_name)
                    st.markdown("---")
                else:
                    st.success(f"✔️ No Reconcillation Failures Observed for Table: {sf_table_name}")
                    st.markdown("---")
                
                #Printe the lineage graph again if more failures are detected
                if len(st.session_state.error_tables) > 1:
                    #st.markdown(f"#### Printing the lineage graph again for {len(st.session_state.error_tables)} failures")
                    st.subheader("🔗Lineage Graph for {len(st.session_state.error_tables)} Failures")
                    sas_col, sf_col = st.columns(2)
                    ###BUG: lineage graph is not highlighting all error tables
                    with sas_col:
                        print_lineage_graph("SAS", sas_table_name, reproduce=True)
                    with sf_col:
                        print_lineage_graph("SF", sf_table_name, reproduce=True)
                    st.markdown("---")

# ---------------------------
# Test Report: Summarize all failures using a LLM
# ---------------------------
    #This section should execute despite if the upstream dependencies is clicked or not
    # - merge the validation_results from both the runs (for first run other should be empty anyway)
    # - only keeping the unique records
    # Temporary dictionary to hold unique records
    # The key is a tuple of (Test, SAS Dataset) for uniqueness
    #merged_list = st.session_state["validation_results"] + st.session_state["validation_results_run2"]

    val1 = st.session_state.get("validation_results")
    val2 = st.session_state.get("validation_results_run2")

    dfs = [v for v in (val1, val2) if isinstance(v, pd.DataFrame)]

    if dfs:  # not empty
        df = pd.concat(dfs, ignore_index=True)
    else:
        df = pd.DataFrame()  # empty if nothing present
    df.drop_duplicates(subset=['Test', 'SAS Dataset', 'SF Table', 'SAS Column', 'SF Column'], keep='last', inplace=True)
    merged_list_unique = df.to_dict("records")

    if merged_list_unique is not None:
        st.subheader("✅ Summary Report")

        if st.session_state["any_failures"]:
            #st.error(f"❌ Reconcillation Failures Observed for Table: {sf_table_name}")
            #st.dataframe(st.session_state["upstream_tables"])
            #st.dataframe(st.session_state["error_tables"])
            #Printing the validation_results_run2 after lineage navigation
            #st.dataframe(merged_list_unique)
            llm_response_html = generate_llm_summary(
                        st.session_state["upstream_tables"],
                        st.session_state["error_tables"],
                        merged_list_unique,
                        sas_lineage_pickle,
                        sf_lineage_pickle)

            #Buid nice border and styling to display the summary report
            #Provide a download button to download the report as PDF
            from weasyprint import HTML
            from io import BytesIO
            from datetime import datetime

            # --- Content without Streamlit-specific border ---
            export_html_content = """
            <h1>Hello World</h1>
            <p>This is <b>formatted</b> HTML exported to PDF.</p>
            """

            # --- Content with border (for Streamlit UI only) ---
            ui_html_content = f"""
            <div class="custom-box">
            {llm_response_html}
            </div>
            """
            # CSS for UI only (does NOT go into the PDF)
            st.markdown(
                """
                <style>
                .custom-box {
                    border: 2px solid #4A90E2;
                    border-radius: 8px;
                    padding: 20px;
                    margin-bottom: 10px;
                    background-color: #191414; 
                    font-family: sans-serif, Arial;
                    overflow-x: auto;           /* enable horizontal scrolling */
                    /*max-width: 100%;             keep within page width */
                    display: block;
                }
                .custom-box table {
                    width: 100%;
                    border-collapse: collapse;
                    table-layout: auto;          /* allows columns to shrink */
                }

                .custom-box th, .custom-box td {
                    padding: 8px;
                    text-align: left;
                    white-space: nowrap;         /* prevents breaking words mid-cell */
                }
                </style>
                """,
                unsafe_allow_html=True,
            )

            # --- Show the bordered UI content ---
            st.markdown(ui_html_content, unsafe_allow_html=True)

            # --- PDF export uses clean content (no border) ---
            full_html_for_pdf = f"""
            <html>
            <head>
                <style>
                    /* Base page style */
                    @page {{
                        size: A4;
                        margin: 40px;
                    }}
                    body {{
                        font-family: 'Segoe UI', Arial, sans-serif;
                        color: #333;
                        line-height: 1.6;
                    }}

                    /* Header styles */
                    h1, h2, h3 {{
                        color: #1a4e8a;
                        margin-top: 30px;
                        margin-bottom: 10px;
                    }}
                    h1 {{
                        text-align: center;
                        font-size: 28px;
                        border-bottom: 3px solid #1a4e8a;
                        padding-bottom: 10px;
                    }}
                    h2 {{
                        font-size: 20px;
                        border-left: 5px solid #1a4e8a;
                        padding-left: 10px;
                        margin-top: 40px;
                    }}
                    h3 {{
                        font-size: 16px;
                        margin-top: 25px;
                    }}

                    /* Paragraph and list styles */
                    p {{
                        font-size: 14px;
                        margin: 6px 0;
                    }}
                    ul {{
                        margin: 8px 0 8px 25px;
                    }}
                    li {{
                        margin-bottom: 4px;
                    }}

                    /* Table styles (for test results if you add them) */
                    table {{
                        width: 100%;
                        border-collapse: collapse;
                        margin: 20px 0;
                        font-size: 13px;
                    }}
                    th, td {{
                        border: 1px solid #ccc;
                        padding: 8px 10px;
                        text-align: left;
                    }}
                    th {{
                        background-color: #f0f4f9;
                        color: #1a4e8a;
                    }}
                    tr:nth-child(even) {{
                        background-color: #f9f9f9;
                    }}

                    /* Section box highlight (optional) */
                    .section {{
                        background: #fdfdfd;
                        border: 1px solid #e0e0e0;
                        border-radius: 6px;
                        padding: 15px 20px;
                        margin-bottom: 25px;
                        box-shadow: 0 1px 2px rgba(0,0,0,0.05);
                    }}

                    /* Footer */
                    footer {{
                        text-align: center;
                        font-size: 10px;
                        color: #999;
                        margin-top: 40px;
                    }}
                </style>
            </head>
            <body>
                <h1>Validation Summary Report</h1>
                {llm_response_html}
                <footer>Generated on {datetime.now().strftime("%Y-%m-%d")}</footer>
            </body>
            </html>
            """

            pdf_bytes = HTML(string=full_html_for_pdf).write_pdf()
            pdf_file = BytesIO(pdf_bytes)

            # ----- Toolbar below the box -----
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            toolbar_col1, toolbar_col2 = st.columns([6, 2])
            with toolbar_col2:
                st.download_button(
                    "⬇️ Download PDF",
                    data=pdf_file,
                    file_name=f"Validation_Summary_Report_{timestamp}.pdf",
                    mime="application/pdf"
                )

            st.markdown("---")
        else:
            st.success(f"✔️ No Reconcillation Failures Observed for Table: {sf_table_name}")
            st.markdown("---")
