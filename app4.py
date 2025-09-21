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
# Session State Initialization
# ------------------------------------------------------------------------
from lineage.lineage_functions import generate_lineage_graph, collect_upstream_tables
from helper_functions import decode_value, suggest_columns_for_rule, validate_datasets
from helper_functions import load_validations_from_csv, update_validations_in_csv
from llm_agents.llm_reports import generate_llm_summary

sas_lineage_pickle = "./lineage/SAS_lineage_graph.pkl"
sf_lineage_pickle = "./lineage/SF_lineage_graph.pkl"

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

if "upstream_dependencies_checked" not in st.session_state:
    st.session_state["upstream_dependencies_checked"] = False

if "display_results" not in st.session_state:
    st.session_state["display_results"] = False

if "any_failures_run2" not in st.session_state:
    st.session_state["any_failures_run2"] = None
# ------------------------------------------------------------------------
# Common Functions
# ------------------------------------------------------------------------
#Function to load SAS Datasets to a Dataframe
def load_datasets(file_name: str, platform: str, qualified=True):
    if platform == "SAS":
        if not qualified:
            file_name = st.session_state.SAS_path + file_name + ".sas7bdat"

        # Pandas will auto-detect format
        df = pd.read_sas(file_name, format="sas7bdat")

        #Properly decode SAS character variables
        df = df.map(decode_value)
    elif platform == "SF": 
        if not qualified:
            file_name = st.session_state.SF_path + file_name + ".csv"
            df = pd.read_csv(file_name)
        else:
            #df = pd.read_csv(io.StringIO(file_name.getvalue().decode("utf-8")))
            df = pd.read_csv(file_name)
    else:
        df = None

    return df

# ------------------------------------------------------------------------
#Print lineage graph
# ------------------------------------------------------------------------
def print_lineage_graph(platform: str, table_name: str, reproduce=False):
    if platform == "SAS":
        net_key = "sas_lineage"
        if net_key not in st.session_state or reproduce:
            sas_html = generate_lineage_graph(sas_lineage_pickle, st.session_state.sas_table_name, None)
            st.session_state[net_key] = sas_html
        else:
            sas_html = st.session_state[net_key]

        with st.container(border=True):
            st.markdown("#### 🟠 SAS Lineage")
            components.html(sas_html, height=300)
    elif platform == "SF":
        net_key = "sf_lineage"
        if net_key not in st.session_state or reproduce:
            sf_html = generate_lineage_graph(sf_lineage_pickle, table_name, st.session_state.error_tables)
            st.session_state[net_key] = sf_html
        else:
            sf_html = st.session_state[net_key]

        with st.container(border=True):
            st.markdown("#### 🔵 Snowflake Lineage")
            components.html(sf_html, height=300)


def show_lineage_graph(title, sas_table_name, sf_table_name, reproduce=False):
# ---------------------------
# Show Lineage
# ---------------------------
    if st.session_state["display_results"]:
        st.subheader(title)
        sas_col, sf_col = st.columns(2)

        with sas_col:
            print_lineage_graph("SAS", sas_table_name, reproduce)

        with sf_col:
            print_lineage_graph("SF", sf_table_name, reproduce)
# ------------------------------------------------------------------------
# Check Up Stream Dependencies and plot Lineage 
# ------------------------------------------------------------------------
def check_up_stream_dependencies():
# ---------------------------
# Repeat validations for the upstream dataset until clean
# ---------------------------
    #If the checks already performed in earlier runs - do not perform again
    if st.session_state["validation_results_run2"] is None:
        st.info("Checking Upstream Dependencies")
        #1. Get the current failed table as a starting point
        #2. Navigate the lineage upstream and identify upstream tables
        #3. Perform the same validations on these tables
        #4. These tables may not have all the columns as the current table - to be handled

        st.session_state.upstream_tables = []

        new_val = {"table": st.session_state.sf_table_name, "rank": 0}
        st.session_state.upstream_tables.append(new_val)

        # Collect recursively
        st.session_state.upstream_tables.extend(
            collect_upstream_tables(sf_lineage_pickle, st.session_state.sf_table_name)
        )

        #Run a loop on st.session_state.upstream_tables to validate all the tables in the list
        # Filter and sort the tables, starting from rank = 1
        # print the results and the lineage at the end for all error tables
        sorted_tables = sorted(
            [t for t in st.session_state.upstream_tables if t["rank"] >= 1],
            key=lambda x: x["rank"]
        )

        # Loop through each table in rank order
        st.session_state["any_failures_run2"] = False
        for tbl in sorted_tables:
            table_name = tbl["table"]
            rank = tbl["rank"]

            # Example: load data for each table
            try:
                # Try loading the data
                st.session_state.sas_df = load_datasets(table_name, "SAS", False)
                st.session_state.sf_df = load_datasets(table_name, "SF", False)

            except FileNotFoundError as e:
                st.warning(f"Skipping {table_name}: {e}")
                continue  # move to next table

            # Run validation
            st.session_state["any_failures_run2"], new_results = validate_datasets(
                st.session_state.validations_list, 
                st.session_state.sas_df,
                table_name,
                st.session_state.sf_df,
                table_name
            )

            # Append results
            # Concatenate DataFrames instead of extend
            st.session_state["validation_results_run2"] = pd.concat(
                [st.session_state["validation_results"], new_results],
                ignore_index=True
            )
            if st.session_state["any_failures_run2"]:
                st.session_state.error_tables.append(table_name)
        #End For for all Tables
    #End check for earlier runs

    #Write the validation test report
    st.subheader("✅ Validation Results for Upstream Dependencies")
    if st.session_state["any_failures_run2"]:
        st.dataframe(pd.DataFrame(st.session_state["validation_results_run2"]), hide_index=True)
        st.error(f"❌ Reconcillation Failures Observed for Table: {st.session_state.error_tables[-1]}")
        st.markdown("---")
    else:
        st.success(f"✔️ No Reconcillation Failures Observed for Table: {st.session_state.sf_table_name}")
        st.markdown("---")

    #Printe the lineage graph again if more failures are detected
    if len(st.session_state.error_tables) > 1:
        show_lineage_graph(f"🔗Lineage Graph for {len(st.session_state.error_tables)} Failures",
                         st.session_state.sas_table_name,
                         st.session_state.sf_table_name, 
                         reproduce=True)
        st.markdown("---")
    st.session_state["upstream_dependencies_checked"] = True

# ------------------------------------------------------------------------
# Display Validation Summary Report
# ------------------------------------------------------------------------
import base64
def display_validation_test_summary():
# ---------------------------
# Test Report: Summarize all failures using a LLM
# ---------------------------
    if not st.session_state["display_results"]:
        st.stop()

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
#BUG: The report table needs to be truncated - change HTML content
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

            ## ----- Toolbar below the box -----
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            #toolbar_col1, toolbar_col2 = st.columns([6, 2])
            #with toolbar_col2:
            #    st.download_button(
            #        "⬇️ Download PDF",
            #        data=pdf_file,
            #        file_name=f"Validation_Summary_Report_{timestamp}.pdf",
            #        mime="application/pdf"
            #    )
            #Javascript Download Button
            # Convert PDF to base64
            pdf_base64 = base64.b64encode(pdf_file.getvalue()).decode("utf-8")

            # Create custom floating download button with HTML+CSS+JS
            custom_button_html = f"""
            <style>
            #download-btn {{
                position: fixed;
                bottom: 30px;
                right: 30px;
                background-color: #4A90E2;
                color: white;
                border: none;
                padding: 14px 22px;
                border-radius: 8px;
                font-size: 16px;
                font-weight: bold;
                cursor: pointer;
                box-shadow: 0px 4px 8px rgba(0,0,0,0.2);
                z-index: 9999;
                transition: background-color 0.3s ease;
            }}
            #download-btn:hover {{
                background-color: #357ABD;
            }}
            </style>

            <button id="download-btn">⬇️ Download PDF</button>
            <script>
            document.getElementById("download-btn").addEventListener("click", function() {{
                var link = document.createElement('a');
                link.href = "data:application/pdf;base64,{pdf_base64}";
                link.download = "Validation_Summary_Report_{timestamp}.pdf";
                link.click();
            }});
            </script>
            """

            # Render custom button in Streamlit
            #st.markdown(custom_button_html, unsafe_allow_html=True)

            st.markdown("---")
        else:
            st.success(f"✔️ No Reconcillation Failures Observed for Table: {st.session_state.sf_table_name}")
            st.markdown("---")

# ------------------------------------------------------------------------
# Function to control behavioud of the dropdowns for selecting columns
# ------------------------------------------------------------------------
def reset_other_dropdown(changed_key, other_key):
    #Reset all selections to default
    if other_key == "":
        st.session_state["col_select3"] = "-- Select --"
    # If the current dropdown is selected, reset the other
    elif st.session_state[changed_key] != "-- Select --":
        st.session_state[other_key] = "-- Select --"

# ------------------------------------------------------------------------
#Function to load or save validation configurations in json
# ------------------------------------------------------------------------
import json
json_file = "./config/validation_rule_config.json"
def load_validation_config(config):
    if config:
        with open(json_file, "w") as f:
            json.dump(config, f, indent=2)
        st.session_state.validation_config = config
    else:
        data = []
        # Step 1: Load file if exists
        if os.path.exists(json_file):
            with open(json_file, "r") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    data = []  # fallback if file is corrupted
        st.session_state.validation_config = data

##########################################################################
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

#Select Environments
# Example predefined SF file locations
sf_file_options = {
    "Development (Scenario 3)": "./sample_data/Scenario2/",
    "Integration Test (Scenario 2)": "./sample_data/Scenario1/",
    "Production (Scenario 1)": "./sample_data/"
}

# Use radio buttons for single selection
selected_sf_source = st.sidebar.radio(
    "Select Environment (Scenario):",
    options=list(sf_file_options.keys())
)

# Upload files
st.sidebar.header("Upload SAS Dataset for Validation")
# File uploader with a fixed key
sas_file = st.sidebar.file_uploader(
    "Upload SAS dataset (.sas7bdat or .xpt)",
    type=["sas7bdat", "xpt"],
    key="sas_file"  # fixed key to track uploader
)

#sf_file = st.sidebar.file_uploader("Upload Snowflake migrated data (CSV)", type=["csv"])
# File uploader with a fixed key
#sf_file = st.sidebar.file_uploader(
#    "Upload Snowflake migrated data (*.csv)",
#    type=["csv"],
#    key="sf_file"  # fixed key to track uploader
#)
# Initialize session state
if "sas_table_name" not in st.session_state:
    st.session_state.sas_df = None
    st.session_state.sf_df = None
    st.session_state.sas_table_name = ""
    st.session_state.sf_table_name = ""
    st.session_state.SAS_path = ""
    st.session_state.SF_path = ""

if sas_file and st.session_state.sas_table_name == "":
    try:
        #Load SAS datasets to a DataFrame
        st.session_state.SAS_path = "./sample_data/"
        st.session_state.sas_df = load_datasets(sas_file, "SAS")
        #st.session_state.sas_df.columns = st.session_state.sas_df.columns.str.lower()
        st.session_state.sas_table_name = os.path.splitext(sas_file.name)[0]  # remove extension
        sas_table_name=st.session_state.sas_table_name

        st.success(f"SAS dataset loaded: {st.session_state.sas_df.shape[0]} rows, {st.session_state.sas_df.shape[1]} cols")
        #Load the persisted validations for this table
        # Load validations only first time
        st.session_state.validations_list = load_validations_from_csv(sas_table_name)

        # Mark as processed
        st.session_state.sas_loaded = True

        #Auto select the Snowflake CSV based on selected Scenario
        st.session_state.SF_path = sf_file_options[selected_sf_source]
        try:
            #Assumption: SAS and Sowflake will have the same table names 
            # - otherwise a mapping needs to be stored and retrieved
            st.session_state.sf_table_name= st.session_state.sas_table_name
            st.session_state.sf_df = load_datasets(st.session_state.sf_table_name, "SF", False)
            #st.session_state.sf_table_name = os.path.splitext(sf_path.name)[0]  # remove extension
            st.success(f"Snowflake CSV loaded: {st.session_state.sf_df.shape[0]} rows, {st.session_state.sf_df.shape[1]} cols")
            st.session_state.sf_loaded = True
        except Exception as e:
            st.error(f"❌ Error reading CSV: {e}")
            st.stop()
    except Exception as e:
        st.error(f"❌ Error reading SAS dataset: {e}")
        st.stop()
elif sas_file is None:
    st.info("Please upload SAS dataset to start building validation rules.")
    st.stop()

#if sf_file and not st.session_state.sf_loaded:
#    try:
#        st.session_state.sf_df = load_datasets(sf_file, "SF")
#        st.session_state.sf_table_name = os.path.splitext(sf_file.name)[0]  # remove extension
#
#        st.success(f"Snowflake CSV loaded: {st.session_state.sf_df.shape[0]} rows, {st.session_state.sf_df.shape[1]} cols")
#        # Mark as processed
#        st.session_state.sf_loaded = True
#    except Exception as e:
#        st.error(f"❌ Error reading Snowflake CSV: {e}")


# ---------------------------
# Preview Selected Datasets
# ---------------------------
if st.session_state.sas_df is not None and st.session_state.sf_df is not None:
    st.subheader("📊 Preview Uploaded Datasets")
    tab1, tab2 = st.tabs(["Preview SAS Dataset", "Preview Snowflake Dataset"])
    with tab1:
        st.subheader(f"**SAS Baseline : {st.session_state.sas_table_name} **")
        st.table(st.session_state.sas_df.head())
        #st.image("https://static.streamlit.io/examples/cat.jpg", width=200)
    with tab2:
        st.subheader(f"**Snowflake Data: {st.session_state.sf_table_name} **")
        #st.dataframe(st.session_state.sf_df.head(), hide_index=True)
        st.table(st.session_state.sf_df.head())

# ---------------------------
# Validation Selection - Configuration
# ---------------------------
    st.markdown("---")
    st.subheader("⚙️ Configure Validations")
    #Get the config file json and stor it as session variable
    if "validation_config" not in st.session_state:
        load_validation_config([])
    
    col1, col2, col3 = st.columns(3)

    with col1:
        rule = st.selectbox(
            "Select Validation Type",
            ["Row Count", "Sum Amount", "Distinct Count", "Uniqueness", "Not Null",  "Row Hash"],
            key="rule_select",
            on_change=reset_other_dropdown,
            args=("col_select1", ""),
        )

    selected_col = None
    hash_file = None

    with st.spinner("Fetching AI recommendations..."):
        with col2:
            if rule in ["Sum Amount", "Distinct Count", "Uniqueness", "Not Null"]:
                is_updated, config, suggestions = suggest_columns_for_rule(
                                    st.session_state.sas_table_name,
                                    rule.lower().replace(" ", "_"), 
                                    st.session_state.sas_df,
                                    st.session_state.validation_config
                                    )
                if is_updated:
                    load_validation_config(config)

                # Ensure suggestions is a list
                if isinstance(suggestions, str):
                    import ast
                    suggestions = ast.literal_eval(suggestions)  # converts string representation of list to actual list

                options = ["-- Select --"] + suggestions

                # Default index logic
                index2 = 0 if not suggestions else options.index(suggestions[0])

                selected_col2 = st.selectbox(
                    "Select AI Recommended Column",
                    options, 
                    index=index2,
                    key="col_select2",
                    on_change=reset_other_dropdown,
                    args=("col_select2", "col_select3"),
                )
        with col3:
            if rule in ["Sum Amount", "Distinct Count", "Uniqueness", "Not Null"]:
                if "col_select3" not in st.session_state:
                    st.session_state["col_select3"] = "-- Select --"
                options = ["-- Select --"] + list(st.session_state.sas_df.columns)
                selected_col3 = st.selectbox(
                    "Select Column (All Columns)",
                    options,
                    key="col_select3", # don't use index when using session_state
                    on_change=reset_other_dropdown,
                    args=("col_select3", "col_select2")
                )
    #End of spinner block

    # Final check: ensure at least one selected
    if (
        rule in ["Sum Amount", "Distinct Count", "Uniqueness", "Not Null"]
        and st.session_state["col_select2"] == "-- Select --"
        and st.session_state["col_select3"] == "-- Select --"
    ):
        st.error("⚠️ Please select a column from at least one dropdown.")
        st.stop()
    elif rule in ["Sum Amount", "Distinct Count", "Uniqueness", "Not Null"]:
        # Resolve final selection
        if st.session_state["col_select2"] != "-- Select --":
            selected_col = st.session_state["col_select2"]
        elif st.session_state["col_select3"] != "-- Select --":
            selected_col = st.session_state["col_select3"]
        else:
            selected_col = None

    if rule == "Row Hash":
        hash_file = st.file_uploader("Upload SAS Hash File", type=["csv"], key="hash_upload")

    if st.button("➕ Add Validation", key=f"{rule}_{selected_col}_add_rule"):
        # Find existing rule by table + attribute + dq_dimension
        selected_col = selected_col if selected_col else "NA"
        match = next(
            (r for r in st.session_state.validations_list
            if r["rule"] == rule
            and r["column"] == selected_col),
            None
        )

        if match:
            st.info("This rule already exists. ✅")
        else:
            st.session_state.validations_list.append({
                "rule": rule,
                "column": selected_col.strip() if selected_col and selected_col.strip() else "NA",
                "hash_df": pd.read_csv(hash_file) if hash_file else "NA",
                "code": ""
            })
            st.success("Rule added successfully. ➕")
            
        #Reset all session states for any changes to validations
        st.session_state["any_failures"] = None
        st.session_state["display_results"] = False
        st.session_state["any_failures_run2"] = None
        st.session_state["validation_results"] = None
        st.session_state["validation_results_run2"] = None
        st.session_state["upstream_dependencies_checked"] = False
# ---------------------------
# Show Persisted List
# ---------------------------
    st.markdown("---")
    st.subheader("📋 Selected Validations")
    if len(st.session_state.validations_list) == 0:
        st.info("No validations added yet.")
        st.session_state["display_results"] = False
    else:
        # Convert validations list to DataFrame
        df = pd.DataFrame(st.session_state.validations_list)
        # Only keep desired columns, rename headers
        df = df[["rule", "column", "hash_df"]].rename(
            columns={"rule": "Rule", "column": "Column", "hash_df": "Hash File"}
        )

        # Display Validations
        event = st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="multi-row",
        )

        # Delete button
        selected_validations = event.selection.rows
        if selected_validations:
            if st.button("❌ Delete Selected Validations"):
                st.session_state.validations_list = [
                item for i, item in enumerate(st.session_state.validations_list) 
                if i not in selected_validations
                ]
                update_validations_in_csv(st.session_state.sas_table_name, st.session_state.validations_list)
                st.rerun()


    st.markdown("---")
# ---------------------------
# Run Validations
# ---------------------------
    if st.button("🧪 Run All Validations"):
        if "validations_list" not in st.session_state or len(st.session_state.validations_list) == 0:
        #if not st.session_state.get("validations_list"):
            st.error("No validations selected. Select validations to apply them on selected datasets.")
            st.session_state["display_results"] = False
            st.stop()
        else:
            #Update Validations to CSV
            update_validations_in_csv(st.session_state.sas_table_name, st.session_state.validations_list)
            #Reset all session statesfor validations
            st.session_state["validation_results"] = None
            st.session_state["validation_results_run2"] = None
            st.session_state["any_failures"] = None
            st.session_state["upstream_dependencies_checked"] = False
    
            st.session_state["any_failures"], st.session_state["validation_results"]  = validate_datasets(
                st.session_state.validations_list, 
                st.session_state.sas_df,
                st.session_state.sas_table_name,
                st.session_state.sf_df,
                st.session_state.sf_table_name
                )
            #Reset the lineage for each change in validations (button clicked)
            st.session_state.pop("sas_lineage", None)
            st.session_state.pop("sf_lineage", None)

            st.session_state.error_tables = []
            st.session_state.error_tables.append(st.session_state.sf_table_name)
    #End if - Run all Validations
# ---------------------------
# Show Validations Results
# ---------------------------
    if st.session_state["validation_results"] is not None or st.session_state["display_results"]:
        st.subheader("✅ Validation Results")

        if st.session_state["any_failures"]:
            st.error(f"❌ Reconcillation Failures Observed for Table: {st.session_state.error_tables[-1]}")
            st.session_state["display_results"] = True

            #Display Validation Results
            st.dataframe(pd.DataFrame(st.session_state["validation_results"]), hide_index=True)
            st.markdown("---")

            #Show Lineage
            show_lineage_graph("🔗Lineage", st.session_state.sas_table_name, st.session_state.sf_table_name)
            st.markdown("---")

            if st.button("⬆️🔗❓ Check Upstream Dependencies?"):
                with st.spinner("Validating all upstream dependencies ..."):
                    check_up_stream_dependencies()
            #    #Show Summary
            if st.button("📝 Generate Summary Report"):
                if st.session_state["upstream_dependencies_checked"]:
                    check_up_stream_dependencies()
                with st.spinner("Generating Summary Report..."):
                    display_validation_test_summary()
        else:
            st.success(f"✔️ No Reconcillation Failures Observed for Table: {st.session_state.sf_table_name}")
            st.markdown("---")
            st.session_state["display_results"] = False
            st.stop()
    #End if - Show Validations results
