from lineage.lineage_functions import collect_direct_target_jobs

#-----------------------------------------------------------------------------------------
#generate a prompt with lineage, upstream tables, error tables and validation_results_run2
#-----------------------------------------------------------------------------------------
def generate_llm_summary(upstream_tables,error_tables,validation_results_run2,sas_lineage_pickle, sf_lineage_pickle):
    #Step 1: Collect additional information about the transformation jobs for each error tables 
    #        If this is the firt table of the lineage there is no job information
    for table in error_tables:
        #Collect job and code information for SAS
        sas_jobs = collect_direct_target_jobs(sas_lineage_pickle, table)
        #print(f"The Table with Reconcillation Errors: {table}")
        #if jobs:
        #    for job in jobs:
        #        print(f"Is Loaded by Job: {job['Job_Name']}")
        #        print(f"  Using Source Code: {job['Source_Code']}")
        #        print(f"  Key Code Snippet: {job['Highlights_Code_Snippet']}")
        #else:
        #    print("No upstream jobs found for table, therefore this is the source table")

        #Collect job and code information for Snoflake
        sf_jobs = collect_direct_target_jobs(sf_lineage_pickle, table)
        #print(f"The Table with Reconcillation Errors: {table}")
        #if jobs:
        #    for job in jobs:
        #        print(f"Is Loaded by Job: {job['Job_Name']}")
        #        print(f"  Using Source Code: {job['Source_Code']}")
        #        print(f"  Key Code Snippet: {job['Highlights_Code_Snippet']}")
        #else:
        #    print("No upstream jobs found for table, therefore this is the source table")

    #Step 2: Generate Prompt
    print(">>>\nPrinting from LLM")
    print(upstream_tables)
    print(error_tables)
    print(validation_results_run2)
    print(sas_jobs)
    print(sf_jobs)
    #Step 3: Get LLM response
    
    return "LLM Output"