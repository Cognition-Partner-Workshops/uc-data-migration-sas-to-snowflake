import io
import os
import streamlit as st
import pandas as pd
import json
import random
from typing import Dict, List

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
#streamlit run app2.py

# ---------------------------
# Common Functions
# --------------------------
#Convert the raw byte values (ASCII codes) instead of decoded strings
def decode_value(x):
    if isinstance(x, (bytes, bytearray)):
        #E.g. b'Alice' → decoded to "Alice"
        #.strip() cleans trailing spaces from SAS fixed-width CHAR fields.
        return x.decode("utf-8", errors="ignore").strip()
    if isinstance(x, (list, tuple)) or hasattr(x, "__iter__") and not isinstance(x, str):
        try:
            #Example [65,108,105,99,101] → converted to bytes([65,108,105,99,101]) 
            # = b"Alice" → "Alice"
            #.strip() cleans trailing spaces from SAS fixed-width CHAR fields.
            return bytes(x).decode("utf-8", errors="ignore").strip()
        except Exception:
            return x
    return x
# ---------------------------
# Session state initialization
# ---------------------------
if "validations_list" not in st.session_state:
    st.session_state.validations_list = []

# ---------------------------
# Helper function: fake LLM suggestions (replace with real LLM call)
# ---------------------------
def suggest_columns_for_rule(rule, df):
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    string_cols = df.select_dtypes(include="object").columns.tolist()
    
    if rule == "sum_amount":
        return numeric_cols[:3]  # top 3 numeric candidates
    elif rule == "distinct_count":
        return string_cols[:3]   # top 3 categorical candidates
    elif rule == "not_null":
        return df.columns[:5].tolist()  # suggest first 5 columns
    elif rule == "uniqueness":
        return string_cols[:2] + numeric_cols[:1]
    return []
# ---------------------------

# ---------------------------
# Simulated Knowledge Base (Mapping + Validation Templates)
# ---------------------------
MAPPINGS = {
    "customer": {
        "sas_table": "saslib.customer",
        "sf_table": "landing.customer",
        "columns": [
            {"src": "cust_id", "tgt": "cust_id", "transform": "CAST"},
            {"src": "first_name", "tgt": "first_name"},
            {"src": "last_name", "tgt": "last_name"},
            {"src": "email", "tgt": "email", "pii": True, "masking": "hash_email"},
            {"src": "birth_dt", "tgt": "birth_dt", "transform": "SAS_DATE_TO_DATE"},
            {"src": "is_active", "tgt": "is_active", "transform": "0/1 to BOOLEAN"},
        ],
    },
    "transaction": {
        "sas_table": "saslib.transaction",
        "sf_table": "landing.transaction",
        "columns": [
            {"src": "tran_id", "tgt": "tran_id"},
            {"src": "cust_id", "tgt": "cust_id"},
            {"src": "tran_dt", "tgt": "tran_dt", "transform": "SAS_DATETIME_TO_TIMESTAMP"},
            {"src": "amount", "tgt": "amount"},
            {"src": "currency", "tgt": "currency"},
            {"src": "product_id", "tgt": "product_id"},
        ],
    },
}

VALIDATION_TEMPLATES = [
    {"name": "row_count", "desc": "Row counts should match"},
    {"name": "sum_amount", "desc": "SUM(amount) should match within tolerance"},
    {"name": "distinct_cust", "desc": "Distinct customers should match"},
    {"name": "null_email", "desc": "No null emails allowed"},
]

# ---------------------------
# Helper functions
# ---------------------------
def generate_validation_tests(table: str) -> List[Dict]:
    """Simulate LLM generating tests from templates + mappings"""
    tests = []
    if table == "customer":
        tests.append({"name": "row_count", "sql": "SELECT COUNT(*) FROM landing.customer", "tolerance": 0})
        tests.append({"name": "null_email", "sql": "SELECT COUNT(*) FROM landing.customer WHERE email IS NULL", "tolerance": 0})
    if table == "transaction":
        tests.append({"name": "row_count", "sql": "SELECT COUNT(*) FROM landing.transaction", "tolerance": 0})
        tests.append({"name": "sum_amount", "sql": "SELECT SUM(amount) FROM landing.transaction", "tolerance": 0.001})
        tests.append({"name": "distinct_cust", "sql": "SELECT COUNT(DISTINCT cust_id) FROM landing.transaction", "tolerance": 0})
    return tests

# ---------------------------
# Streamlit UI
# ---------------------------
st.title("🔍 Agentic RAG Migration Validation & Testing (SAS → Snowflake Demo)")

# ---------------------------
# Reset button in sidebar
# ---------------------------
import streamlit as st

if st.sidebar.button("🔄 Reset App"):
    for key in st.session_state.keys():
        del st.session_state[key]
    st.rerun()

st.sidebar.header("Upload Data")
sas_file = st.sidebar.file_uploader("Upload SAS dataset (.sas7bdat or .xpt)", type=["sas7bdat", "xpt"])
sf_file = st.sidebar.file_uploader("Upload Snowflake migrated data (CSV)", type=["csv"])
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


if sas_df is not None and sf_df is not None:
    st.subheader("📊 Preview Uploaded Data")
    st.write(f"**SAS Baseline : {sas_table_name} **")
    st.dataframe(sas_df.head())
    st.write(f"**Snowflake Data: {sf_table_name} **")
    st.dataframe(sf_df.head())

    # ---------------------------
    # Validation Selection
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
        new_val = {"rule": rule}
        if selected_col:
            new_val["column"] = selected_col
        if hash_file:
            new_val["hash_df"] = pd.read_csv(hash_file)

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
        st.table(st.session_state.validations_list)

    st.markdown("---")
    # ---------------------------
    # Run Validations
    # ---------------------------
    if st.button("🚀 Run All Validations"):
        results = []

        for val in st.session_state.validations_list:
            rule = val["rule"]

            if rule == "Row Count":
                rc_sas, rc_sf = len(sas_df), len(sf_df)
                status = "PASS" if rc_sas == rc_sf else "FAIL"
                results.append({"Test": "Row Count", "Column": "NA", "SAS Row Count": rc_sas, "SF Row Count": rc_sf, "Status": status})

            elif rule == "Sum Amount":
                col = val["column"]
                sa_sas, sa_sf = sas_df[col].astype(float).sum(), sf_df[col].astype(float).sum()
                status = "PASS" if abs(sa_sas - sa_sf) < 0.01 else "FAIL"
                results.append({"Test": "Sum", "Column": col, "SAS Row Count": sa_sas, "SF Row Count": sa_sf, "Status": status})

            elif rule == "Distinct Count":
                col = val["column"]
                dc_sas, dc_sf = sas_df[col].nunique(), sf_df[col].nunique()
                status = "PASS" if dc_sas == dc_sf else "FAIL"
                results.append({"Test": "Distinct", "Column": col, "SAS Row Count": dc_sas, "SF Row Count": dc_sf, "Status": status})

            elif rule == "Not Null":
                col = val["column"]
                nn_sas, nn_sf = sas_df[col].isna().sum(), sf_df[col].isna().sum()
                status = "PASS" if nn_sas == nn_sf == 0 else "FAIL"
                results.append({"Test": "Not Null", "Column": col, "SAS Row Count": nn_sas, "SF Row Count": nn_sf, "Status": status})

            elif rule == "Uniqueness":
                col = val["column"]
                uq_sas, uq_sf = sas_df[col].is_unique, sf_df[col].is_unique
                status = "PASS" if uq_sas and uq_sf else "FAIL"
                results.append({"Test": "Uniqueness", "Column": col, "SAS Row Count": uq_sas, "SF Row Count": uq_sf, "Status": status})

            elif rule == "Row Hash":
                hash_df = val["hash_df"]
                match = hash_df.equals(sf_df)
                status = "PASS" if match else "FAIL"
                results.append({"Test": "Row Hash", "Column": "NA", "SAS Row Count": len(hash_df), "SF Row Count": len(sf_df), "Status": status})

        st.subheader("✅ Validation Results")
        st.dataframe(pd.DataFrame(results))

        st.markdown("---")
        # ---------------------------
        # Show Lineage
        # ---------------------------
