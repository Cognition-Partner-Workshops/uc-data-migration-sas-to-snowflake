# Agentic RAG based Validation System for SAS to Snowflake Migration

### Streamlit Help
https://docs.streamlit.io/develop/tutorials/elements/dataframe-row-selections

# SAS® OnDemand for Academics
### Free SAS Viya Platform 
https://welcome.oda.sas.com/
User ID: chanakya.kothrud@gmail.com
Pwd: eZpas$w0rd
Launch SAS Viya
SAS User: 

Sample Datasets: https://support.sas.com/documentation/onlinedoc/viya/examples.htm

Steps to access SAS OnDemand for Academics
Register yourself and create your account by visiting registration page
Submit the required details (first name, last name and Email ID) in the registration page.
You will get an email from SAS team with the link to activate your profile.
You need to enter your email address and password information and accept the license agreement and then click Create Account.
After completing step 4, you will get an email with the subject 'You are ready to start using SAS OnDemand for Academics' and user id. Click on the link specified in the email.
Enter your user id and password to log in to the software.
Click on SAS Studio link on the dashboard page.

### SAS Code to convert CSV to .sas7bdat
/* Create a library reference */
libname mydata '/home/u64332413/my_sas_data'; 
/* IMPORTANT: Replace your_user_id with the ID you received from SAS */

/* Import the CSV to create the sas7bdat dataset */
PROC IMPORT DATAFILE='/home/u64332413/creditscores.csv'
    OUT=mydata.creditscores
    DBMS=CSV
    REPLACE;
RUN;
