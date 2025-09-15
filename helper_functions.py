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
                      sas_df: pd.DataFrame, sf_df: pd.DataFrame
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
        
    results_df = pd.DataFrame(results)
    any_failures = (results_df['Status'] == 'FAIL').any()
    return any_failures, results_df

# ------------------------------------------------------------------------
#Function to clean-up the pyvis html string to remove borders and background
# ------------------------------------------------------------------------
# Example usage:
# cleaned_html = clean_html_and_add_css(original_html_string)
from bs4 import BeautifulSoup, NavigableString, Comment

def clean_pyvis_html(html_content: str, only_remove_block=True) -> str:
    """Remove empty <center><h1></h1></center> and add CSS block at end of <head>."""

    soup = BeautifulSoup(html_content, "html.parser")

    # ---- Step 1: Remove <center><h1></h1></center> blocks ----
    # ---- Remove <center> blocks that only contain an empty <h1> ----
    for center in list(soup.find_all("center")):
        # Build list of significant children (ignore whitespace strings and comments)
        significant_children = []
        for child in center.contents:
            # Skip comments entirely
            if isinstance(child, Comment):
                continue
            # Skip navigable strings that are only whitespace/newlines
            if isinstance(child, NavigableString):
                if child.string is None:
                    continue
                if child.string.replace("\xa0", "").strip() == "":
                    continue
                # non-empty text -> significant
                significant_children.append(child)
            else:
                # a Tag (e.g., <h1>) -> significant
                significant_children.append(child)

        # If exactly one significant child and it's an <h1> whose text is empty -> remove the center
        if len(significant_children) == 1:
            child = significant_children[0]
            if getattr(child, "name", None) == "h1":
                h1_text = child.get_text()
                if h1_text is None:
                    h1_text = ""
                # normalize NBSP and whitespace
                if h1_text.replace("\xa0", "").strip() == "":
                    center.decompose()


    if only_remove_block:
        return str(soup)
     
    # ---- Step 2: CSS block to insert ----
    css_block = """
    body {
        margin: 0 !important;
        padding: 0 !important;
    }

    #mynetwork {
        width: 100%;
        height: 100vh; /* take full viewport height */
        background-color: #222222;
        border: none !important;
    }

    .card {
        border: none !important;
        margin: 0 !important;
    }

    .card-body {
        padding: 0 !important;
    }
    """

    style_tag = soup.new_tag("style", type="text/css")
    style_tag.string = css_block.strip()

    # ---- Step 3: Append CSS block ----
    if soup.head:
        soup.head.append(style_tag)
    else:
        new_head = soup.new_tag("head")
        new_head.append(style_tag)
        if soup.html:
            soup.html.insert(0, new_head)
        else:
            new_html = soup.new_tag("html")
            new_html.append(new_head)
            new_html.append(soup)
            soup = new_html

    return str(soup)
