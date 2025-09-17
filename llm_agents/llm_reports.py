import os
###########################################################################
# Depending on using Hugging Face APIs or Google Gemini APIs
# Comment/Uncomment the code accordingly - marked ###
###########################################################################
### from huggingface_hub import InferenceClient
import google.generativeai as genai #for using Gemini API 
from lineage.lineage_functions import collect_direct_target_jobs

### HUGGINGFACE_TOKEN = os.environ.get("HUGGINGFACE_TOKEN", "YOUR_HF_TOKEN")
#1. Initiate Google Gemini
# Get the API key from environment variables
api_key = os.environ.get("GEMINI_API_KEY")
api_key = "AIzaSyB1GhS5BVJH6PkZaBZT0Gx"
if not api_key:
    raise ValueError("GEMINI_API_KEY environment variable not set")

# Configure the API client
genai.configure(api_key=api_key)

#Function to create the transformation job details for error tables
def transformation_details(platform, jobs, table):
    transformation_job_details = f"{platform} table {table} "
    if jobs:
        for job in jobs:
            transformation_job_details = transformation_job_details + (
            f"Is Loaded by Job: {job['Job_Name']}\" "
            f"Using Source Code: {job['Source_Code']}\" "
            f"Key Code Snippet: {", ".join(job['Highlights_Code_Snippet'])}")
    else:
        transformation_job_details = transformation_job_details + " has no upstream jobs, therefore this is the source table"
    
    return transformation_job_details
#-----------------------------------------------------------------------------------------
#generate a prompt with lineage, upstream tables, error tables and validation_results_run2
#-----------------------------------------------------------------------------------------
def generate_llm_summary(upstream_tables,error_tables,validation_results,sas_lineage_pickle, sf_lineage_pickle):
    #Step 1: Collect additional information about the transformation jobs for each error tables 
    #        If this is the firt table of the lineage there is no job information
    transformation_job_details = []
    for table in error_tables:
        #Collect job and code information for SAS
        sas_jobs = collect_direct_target_jobs(sas_lineage_pickle, table)
        transformation_job_details.append(transformation_details("SAS", sas_jobs, table))

        #Collect job and code information for Snoflake
        sf_jobs = collect_direct_target_jobs(sf_lineage_pickle, table)
        transformation_job_details.append(transformation_details("Snoflake", sf_jobs, table))

    #Step 2: Generate Prompt
    #print(">>>\nPrinting from LLM")

    prompt = f"""
    You are a data migration validation expert. You are given the results of a post-migration
    validation and reconciliation between legacy SAS tables and Snowflake tables.

    Your task is to generate a clear, concise, and professional HTML report body (HTML <body> only, no <html> or <head> tags)
    summarizing:

    1. The scope: Validations test are conducted on all Upstream Tables listed below.
    2. Validations test are applied to the same tables and/or columns on SAS and Snowflake
    3. Error means the outcomes of the validations test performed on a table do not match across SAS and Snowflake
    4. The error tables: are the tables where validation errors are found.
    5. The Validation test Results includs:
    - Table name
    - Column name (if applicable)
    - Metric type (Sum/Count)
    - Values for SAS and Snowflake
    - Pass/Fail status
    6. The transformation jobs are the programs loading the tables including:
    - Job_Name
    - Source_Code path
    - Key Code Snippet (highlight important logic related to the error)
    7. Root cause analysis based on the validation failures and the code snippets.
    8. Provide a summary conclusion highlighting data quality risks and suggested fixes.

    Use the following input data:

    **Validation Results:**
    {validation_results}

    **List of all Upstream Tables assessed:**
    {upstream_tables}

    **Tables with validation errors:**
    {error_tables}

    **Details on the Transformation Jobs loading the tables with validation errors:**
    {"\n ".join(transformation_job_details)}

    Output strictly valid HTML for <body> only, using semantic structure:
    - Use <section> for major sections like Scope, Findings, Root Cause, and Recommendations.
    - Use <table> to tabulate validation results (columns: Table, Column, Metric, SAS Value, Snowflake Value, Status).
    - Use <pre><code> for showing code snippets.
    - Use <ul> and <li> for bullet lists.

    The tone should be executive-friendly but precise and technical enough for engineering teams.
    """
    #print(prompt)
    #Step 3: Get LLM response

    ### This section marked ### is to use Hugging Face model APIs
    ###client = InferenceClient(
    ###    provider="cohere",
    ###    api_key=os.environ["HUGGINGFACE_TOKEN"],
    ###)
    #### Call the text generation API
    ###completion = client.chat.completions.create(
    ###    model="CohereLabs/command-a-reasoning-08-2025",
    ###    messages=[
    ###        {
    ###            "role": "user",
    ###            "content": prompt
    ###        }
    ###    ],
    ###)
    ###llm_response = completion.choices[0].message["content"]
    ###print(completion.choices[0].message["reasoning_content"])

    ### This section  is usins google Gemini  APIs
    try:
        model = genai.GenerativeModel("gemini-1.5-flash") #gemini-1.5-flash
        generation_config = genai.GenerationConfig(
            max_output_tokens=512,
            temperature=0.2,
            top_p=0.8,
            #response_mime_type="application/json",
        )

        #llm_response = model.generate_content(
        #    prompt,
        #    generation_config=generation_config
        #)
        llm_response = model.generate_content(prompt)
    except Exception as e:
        llm_response = f"# Error accessing Gemini: {e}"

    return llm_response