import os
import ast
import pandas as pd
import google.generativeai as genai #for using Gemini API 

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY environment variable not set")

# Configure the API client
genai.configure(api_key=api_key)

#-----------------------------------------------------------------------------------------
#-----------------------------------------------------------------------------------------
def agent_get_col_reco(rule: str, df: pd.DataFrame):
    response_text = []
    """
    Ask Gemini which columns are suitable for given validations.
    """
    # Sample a few rows to provide context (not entire dataset for cost/perf reasons)
    sample_data = df.head(5).to_dict(orient="records")
    
    prompt = f"""
You are an expert data quality validator for a SAS-to-Snowflake parallel run validation.
You are given:

- A dataset schema: {list(df.columns)}
- Sample rows: {sample_data}
- Requested validations: {rule.replace("_", " ").title()}

Task:
For each requested validation, recommend the most suitable dataset columns.
Rules:
- Consider both the column name and the actual sample values.
- Example: Do NOT suggest date columns (like YYYYMM) for "Sum Amount" even if numeric.
- Example: Balance, Amount, or numeric financial metrics are valid for "Sum Amount".
- For "Distinct Count" or "Uniqueness", prefer identifiers like customer_id or account_id.
- For "Not Null", all non-technical columns are candidates, but prioritize business keys.
- For "Row Hash" or "Row Count", no column is needed (they apply to the whole table).

Output strictly in list format:
["col1", "col2"]
    """
    ### This section  is using google Gemini  APIs
    try:
        model = genai.GenerativeModel("gemini-2.5-flash") #gemini-1.5-flash
        generation_config = genai.GenerationConfig(
            max_output_tokens=4096,
            temperature=0.2,
            top_p=0.8,
            response_mime_type="text/plain",
        )

        #prompt = "give me a python code for adding two strings Str1 and Str2"
        response = model.generate_content(
            prompt,
            generation_config=generation_config
        )
        #response = model.generate_content(prompt)
        #print(f">>>\nPrinting from LLM {response}")
        response_text = response.text.strip()

    except Exception as e:
        print(f"# Error accessing Gemini: {e}")
        print(f">>>\nPrinting from LLM {response_text}")
        response_text = []
  
    # Ensure suggestions is a list
    if isinstance(response_text, str):
        # converts string representation of list to actual list
        response_text = ast.literal_eval(response_text)  

    return response_text
