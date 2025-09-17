import pickle

# Assume a function `load_lineage_graph` exists to deserialize the pickle file.
def load_lineage_graph(lineage_pickle):
    """Loads a graph from a pickle file."""
    try:
        with open(lineage_pickle, 'rb') as f:
            return pickle.load(f)
    except FileNotFoundError:
        print(f"Error: The file {lineage_pickle} was not found.")
        return None

#Collects the jobs that directly target the start_table.
def collect_direct_target_jobs(lineage_pickle, target_table):
    """
    Args:
        lineage_pickle (str): The path to the pickled graph file.
        start_table (str): The name of the table to find direct jobs for.

    Returns:
        list: A list of dictionaries, where each dictionary represents a job
              and contains its name, code, and highlights.
              Returns None if the start_table is not in the graph.
    """
    G = load_lineage_graph(lineage_pickle)
    if G is None or target_table not in G.nodes:
        return None

    direct_jobs = []

    # Iterate through all direct predecessors of the start_table
    for predecessor in G.predecessors(target_table):
        # Check if the predecessor node is a "job"
        if G.nodes[predecessor].get("type") == "job":
            job_attributes = G.nodes[predecessor]
            job_info = {
                "Job_Name": predecessor,
                "Source_Code": job_attributes.get("code"),
                "Highlights_Code_Snippet": job_attributes.get("highlights"),
            }
            direct_jobs.append(job_info)
            
    return direct_jobs

from llm_agents.llm_reports import generate_llm_summary

# Example of how you might use the function
if __name__ == '__main__':
    sas_lineage_pickle = "./lineage/SAS_lineage_graph.pkl"
    sf_lineage_pickle = "./lineage/SF_lineage_graph.pkl"

    error_tables = ["MONTHLY_AMB", "CUST_ACCOUNTS", "DAILY_BALANCE"]

    # This example call will look for jobs upstream of "processed_table"
    for table in error_tables:
        jobs = collect_direct_target_jobs(sf_lineage_pickle, table)
        print(f"Error Table: {table}")
        if jobs:
            for job in jobs:
                print(f"Job: {job['Job_Name']}")
                print(f"  Source Code: {job['Source_Code']}")
                print(f"  Highlights: {job['Highlights_Code_Snippet']}")
        else:
            print("No upstream jobs found or table not in graph.")
            
    print(">>>Printing from LLM")
    generate_llm_summary("",error_tables,"", sas_lineage_pickle, sf_lineage_pickle)

