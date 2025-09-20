#Helper Functions
import pandas as pd
from typing import Tuple

# ------------------------------------------------------------------------
#Convert the raw byte values (ASCII codes) instead of decoded strings
# ------------------------------------------------------------------------
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

# ------------------------------------------------------------------------
# Helper function: fake LLM suggestions (replace with real LLM call)
# ------------------------------------------------------------------------
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

# ------------------------------------------------------------------------
#Function to execute valiation rules against the dataset
# ------------------------------------------------------------------------
def validate_datasets(validations_list: list, 
                      sas_df: pd.DataFrame, sas_table_name, 
                      sf_df: pd.DataFrame, sf_table_name
                      ) -> Tuple[bool, pd.DataFrame]:
    any_failures = False
    results = []
    for val in validations_list:
        if not isinstance(val, dict):
            raise TypeError(f"Expected dict in validations_list but got {type(val)} and value: {val}")

        rule = val["rule"]

        if rule == "Row Count":
            rc_sas, rc_sf = len(sas_df), len(sf_df)
            status = "PASS" if rc_sas == rc_sf else "FAIL"
            #results.append({"Test": "Row Count", "Column": "NA", "SAS Row Count": rc_sas, "SF Row Count": rc_sf, "Status": status})
            results.append({
                "Test": "Row Count",
                "SAS Dataset": sas_table_name,   # <-- supply variable for SAS dataset
                "SF Table": sf_table_name,       # <-- supply variable for SF table
                "SAS Column": "NA",              # <-- or actual column name if available
                "SF Column": "NA",               # <-- or actual column name if available
                "SAS Row Count": rc_sas,
                "SF Row Count": rc_sf,
                "Status": status
            })


        elif rule == "Sum Amount":
            col = val["column"]
            sa_sas, sa_sf = sas_df[col].astype(float).sum(), sf_df[col].astype(float).sum()
            status = "PASS" if abs(sa_sas - sa_sf) < 0.01 else "FAIL"
            #results.append({"Test": "Sum", "Column": col, "SAS Row Count": sa_sas, "SF Row Count": sa_sf, "Status": status})
            results.append({
                "Test": "Sum",
                "SAS Dataset": sas_table_name,   # <-- supply variable for SAS dataset
                "SF Table": sf_table_name,       # <-- supply variable for SF table
                "SAS Column": col,              # <-- or actual column name if available
                "SF Column": col,               # <-- or actual column name if available
                "SAS Row Count": sa_sas,
                "SF Row Count": sa_sf,
                "Status": status
            })

        elif rule == "Distinct Count":
            col = val["column"]
            dc_sas, dc_sf = sas_df[col].nunique(), sf_df[col].nunique()
            status = "PASS" if dc_sas == dc_sf else "FAIL"
            #results.append({"Test": "Distinct", "Column": col, "SAS Row Count": dc_sas, "SF Row Count": dc_sf, "Status": status})
            results.append({
                "Test": "Distinct",
                "SAS Dataset": sas_table_name,   # <-- supply variable for SAS dataset
                "SF Table": sf_table_name,       # <-- supply variable for SF table
                "SAS Column": col,              # <-- or actual column name if available
                "SF Column": col,               # <-- or actual column name if available
                "SAS Row Count": dc_sas,
                "SF Row Count": dc_sf,
                "Status": status
            })

        elif rule == "Not Null":
            col = val["column"]
            nn_sas, nn_sf = sas_df[col].isna().sum(), sf_df[col].isna().sum()
            status = "PASS" if nn_sas == nn_sf == 0 else "FAIL"
            #results.append({"Test": "Not Null", "Column": col, "SAS Row Count": nn_sas, "SF Row Count": nn_sf, "Status": status})
            results.append({
                "Test": "Not Null",
                "SAS Dataset": sas_table_name,   # <-- supply variable for SAS dataset
                "SF Table": sf_table_name,       # <-- supply variable for SF table
                "SAS Column": col,              # <-- or actual column name if available
                "SF Column": col,               # <-- or actual column name if available
                "SAS Row Count": nn_sas,
                "SF Row Count": nn_sf,
                "Status": status
            })

        elif rule == "Uniqueness":
            col = val["column"]
            uq_sas, uq_sf = sas_df[col].is_unique, sf_df[col].is_unique
            status = "PASS" if uq_sas and uq_sf else "FAIL"
            #results.append({"Test": "Uniqueness", "Column": col, "SAS Row Count": uq_sas, "SF Row Count": uq_sf, "Status": status})
            results.append({
                "Test": "Uniqueness",
                "SAS Dataset": sas_table_name,   # <-- supply variable for SAS dataset
                "SF Table": sf_table_name,       # <-- supply variable for SF table
                "SAS Column": col,              # <-- or actual column name if available
                "SF Column": col,               # <-- or actual column name if available
                "SAS Row Count": uq_sas,
                "SF Row Count": uq_sf,
                "Status": status
            })

        elif rule == "Row Hash":
            hash_df = val["hash_df"]
            match = hash_df.equals(sf_df)
            status = "PASS" if match else "FAIL"
            #results.append({"Test": "Row Hash", "Column": "NA", "SAS Row Count": len(hash_df), "SF Row Count": len(sf_df), "Status": status})
            results.append({
                "Test": "Row Hash",
                "SAS Dataset": sas_table_name,
                "SF Table": sf_table_name,
                "SAS Column": "NA",
                "SF Column": "NA",
                "SAS Row Count": len(hash_df),
                "SF Row Count": len(sf_df),
                "Status": status
            })
        
    results_df = pd.DataFrame(results)
    #Check if any failures for this particular table name
    if "Status" in results_df.columns:
        any_failures = (
            ((results_df["SF Table"] == sf_table_name) & 
            (results_df["Status"] == "FAIL"))
            .any()
        )
    else:
        any_failures = False
    
    return any_failures, results_df


# ------------------------------------------------------------------------
#Function to persist rules to CSV file
# ------------------------------------------------------------------------
import os

VALIDATIONS_CSV = "./config/validations_list.csv"
def DEPRECIATED_update_validations_in_csv(sas_table_name: str, validations_list: list):
    """
    Update dq_rules_config.csv with validations_list.
    - If no matching rule exists → insert new row
    - If matching rule exists → update in place (no duplicate row)
    - Return only the inserted/updated rules (or empty df if no change)
    """

    if os.path.exists(VALIDATIONS_CSV):
        validations_df = pd.read_csv(VALIDATIONS_CSV, keep_default_na=False)
        # Convert relevant columns to string
        for col in ["rule", "column", "hash_df", "code"]:
            validations_df[col] = validations_df[col].astype(str)
        # Normalize column values: blank/empty/whitespace → "NA"
        validations_df["column"] = validations_df["column"].apply(
            lambda x: "NA" if not x.strip() else x
        )
        #validations_df["hash_df"] = validations_df["hash_df"].apply(
        #    lambda x: "NA" if not x.strip() else x
        #)
    else:
        validations_df = pd.DataFrame(columns=["rule_id", "table", "rule","column","hash_df","code"])

    int_validations_counter = validations_df.shape[0]  # total existing rules
    changed_rules = []  # store only inserted/updated rules

    for rule in validations_list:
        # Filter existing rules for the same table, attribute, dq_dimension
        # Ensure "column" is always a string and replace null/blank/whitespace with "NA"
        rule_column = rule["column"]
        if not rule_column or str(rule_column).strip() == "":
            rule_column = "NA"

        mask = (
            (validations_df["table"] == sas_table_name) &
            (validations_df["rule"] == rule["rule"]) &
            (validations_df["column"].fillna("NA") == rule_column)
        )
        existing_rule = validations_df[mask]

        if existing_rule.empty:
            # Completely new rule → add
            int_validations_counter += 1
            new_row = {
                "rule_id": int_validations_counter,
                "table": sas_table_name,
                "rule": rule["rule"],
                "column": rule_column,
                "hash_df": rule["hash_df"],
                "code": rule["code"]
            }
            validations_df.loc[len(validations_df)] = new_row
            changed_rules.append(new_row)
            print(f"Debug>>\n{validations_df}")
        else:
            # Rule exists → update Hash File path/code if changed
            idx = existing_rule.index[0]
            if (
                validations_df.at[idx, "hash_df"] != rule["hash_df"] or
                validations_df.at[idx, "code"] != rule["code"]
            ):
                validations_df.at[idx, "hash_df"] = rule["hash_df"]
                validations_df.at[idx, "code"] = rule["code"]
                changed_rules.append({
                    "rule_id": validations_df.at[idx, "rule_id"],
                    "table": sas_table_name,
                    "rule": rule["rule"],
                    "column": rule_column,
                    "hash_df": rule["hash_df"] if pd.notna(rule["hash_df"]) and str(rule["hash_df"]).strip() else "NA",
                    "code": rule["code"]
                })
            else:
                #Rule exist in CSV and not in Validations_lists - Delete
                validations_df = validations_df[~mask]

    # Save only if changes happened
    if changed_rules:
        # Replace null/blank/whitespace in "column" with "NA" for the whole dataframe
        validations_df["column"] = validations_df["column"].apply(
            lambda x: "NA" if pd.isna(x) or str(x).strip() == "" else x
        )
        validations_df.to_csv(VALIDATIONS_CSV, index=False)
        changed_df = pd.DataFrame(changed_rules)
        print(f"✅ {len(changed_df)} rule(s) inserted/updated.")
        #return True, changed_df
    else:
        print("No new or updated rules.")
        #return False, pd.DataFrame()

def load_validations_from_csv(sas_table_name: str):
    # Load CSV if it exists
    if os.path.exists(VALIDATIONS_CSV):
        validations_df = pd.read_csv(VALIDATIONS_CSV)
    else:
        validations_df = pd.DataFrame(
            columns=["rule_id", "table", "rule", "column", "hash_df", "code"]
        )

    # Filter by table name
    filtered_df = validations_df[validations_df["table"] == sas_table_name]

    # Keep only required columns
    selected_df = filtered_df[["rule", "column", "hash_df", "code"]]

    # Convert to list of dicts
    return selected_df.to_dict(orient="records")

def update_validations_in_csv(sas_table_name: str, validations_list: list):
    """
    Save validations_list to CSV, adding rule_id and table columns.
    - sas_table_name: table name to add to all rows
    - validations_list: list of dicts containing rules
    - filepath: CSV path to save
    """
    
    if not validations_list:
        print("No validations to save.")
        return
    
    # Convert list of dicts to DataFrame
    df = pd.DataFrame(validations_list)

    # Normalize columns to ensure no NaN/blank
    for col in ["rule", "column", "hash_df", "code"]:
        df[col] = df[col].apply(lambda x: str(x).strip() if x else "NA")

    # Add table column
    df["table"] = sas_table_name

    # Add rule_id (sequential starting from 1)
    df.insert(0, "rule_id", range(1, len(df) + 1))

    desired_order = ["rule_id", "table", "rule", "column", "hash_df", "code"]
    df = df[desired_order]

    # Overwrite CSV
    df.to_csv(VALIDATIONS_CSV, index=False)
    print(f"✅ {len(df)} validations saved to {VALIDATIONS_CSV} (overwritten)")
