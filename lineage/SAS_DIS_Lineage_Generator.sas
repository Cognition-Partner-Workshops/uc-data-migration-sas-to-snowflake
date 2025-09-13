/* Lineage Extraction: Extract all jobs information from the SAS Metadata Server and then transforming it into JSON (Collibra styke Lineage JSON*/
%macro DI_Lineage_to_JSON(output_json=C:\temp\lineage.json);

   /* Step 1: Extract all jobs from metadata */
   filename jobsxml temp;

   proc metadata
      in=
      "<GetMetadataObjects>
         <Reposid>$METAREPOSITORY</Reposid>
         <Type>Job</Type>
         <Objects/>
         <NS>SAS</NS>
         <Flags>385</Flags>
       </GetMetadataObjects>"
      out=jobsxml;
   run;

   /* Step 2: Read XML into SAS datasets */
   libname jobsxml xml;

   /* Step 3: Create a lineage dataset (Job -> Source -> Target) */
   data lineage_raw;
       set jobsxml.Job;
       length job_name $100 source_table $100 target_table $100;

       /* Example parsing: actual XML fields may vary depending on SAS version */
       job_name = Name;

       /* Source tables from Input tables in the job */
       if n(of InputTables[*]) > 0 then do i=1 to dim(InputTables);
           source_table = InputTables[i].Name;
       end;

       /* Target tables from Output tables in the job */
       if n(of OutputTables[*]) > 0 then do j=1 to dim(OutputTables);
           target_table = OutputTables[j].Name;
           output;
       end;
   run;

   /* Step 4: Create Collibra-style JSON nodes and edges */
   data nodes edges;
       set lineage_raw;

       length node_id $100 source_id $100 target_id $100;

       /* Nodes for tables */
       node_id = source_table; output nodes;
       node_id = target_table; output nodes;

       /* Edges for lineage */
       source_id = source_table;
       target_id = target_table;
       output edges;
   run;

   /* Remove duplicate nodes */
   proc sort data=nodes nodupkey; by node_id; run;

   /* Step 5: Generate JSON in Collibra style */
   filename jout "&output_json";

   data _null_;
       file jout lrecl=32767;
       put '{';
       put '"nodes": [';

       /* Write nodes */
       do i=1 to nobs_nodes;
           set nodes nobs=nobs_nodes point=i;
           if i>1 then put ',';
           put '{"id":"' node_id '"}';
       end;

       put '],';
       put '"edges": [';

       /* Write edges */
       do j=1 to nobs_edges;
           set edges nobs=nobs_edges point=j;
           if j>1 then put ',';
           put '{"from":"' source_id '","to":"' target_id '"}';
       end;

       put ']';
       put '}';
   run;

%mend DI_Lineage_to_JSON;

/* Example usage */
%DI_Lineage_to_JSON(output_json=C:\temp\collibra_lineage.json);
