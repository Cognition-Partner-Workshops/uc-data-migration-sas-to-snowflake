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

