Back to [README.md](https://github.com/josephtsang2012/ar_fund_in_automation/blob/dcb5322e992124da49df8a0ac03eb67ed3635c31/README.md)

# Methodology

A systematic approach was used to identify the schema/tables involved in the **AR Fund In** process. Based on these findings, scripts were developed to update the backend database directly, bypassing the normal procedure of creating AR receipts through the P2000 frontend.

## 1. Coordinate with the user department to create an AR receipt in P2000 within a controlled time window

- Run a snapshot of the `COUNTERSTBL` (counter) table shortly **before** the experiment.
- Have the user department create an AR receipt in P2000 during a low-usage period (e.g. lunchtime).
- Run another snapshot of the `COUNTERSTBL` table shortly **after** the experiment.
- Compare the database state before and after the experiment to identify tables that were created or updated by the designated user during that window (see [all_tables.sql](https://github.com/joseph-tsang-topcast/AR-Fund-In/blob/ac68d047f826f71e78683262887d4da060a615df/all_tables.sql), [added_tables.sql](https://github.com/joseph-tsang-topcast/AR-Fund-In/blob/ac68d047f826f71e78683262887d4da060a615df/added_tables.sql), and [updated_tables.sql](https://github.com/joseph-tsang-topcast/AR-Fund-In/blob/ac68d047f826f71e78683262887d4da060a615df/updated_tables.sql).
- After reviewing the impacted tables in detail, tables unrelated to the AR Fund In process were excluded. The scope was narrowed to the following core tables:
  - `ACCOUNT_AR_AP`
  - `CHECK_HDR`
  - `CHECK_LINE`
  - `COUNTERSTBL`
  - (`STOCKIMG` and `GLTRANS` can be ignored for now)
 
## 2. Determine the relationships among the tables
- Database views were excluded from analysis because standard views do not store data; they always reflect the current state of the underlying tables.
- Timestamps present in the tables provide evidence of dependency order (i.e., the sequence in which tables are modified) and indicate whether records are appended or updated.
- Document numbers (`DOC_NO`, `USER_DOC`, etc.) in `ACCOUNT_AR_AP`, `CHECK_HDR`, and `CHECK_LINE` are sourced from the `COUNTERSTBL` (counter) table.
  - The before-and-after snapshots of `COUNTERSTBL` can be used to validate the new document numbers generated during the experiment.
<img width="620" height="438" alt="Screenshot 2026-08-04 100732" src="https://github.com/user-attachments/assets/78d1b8b2-5976-4248-a2a0-6589fef0a9dc" />

## 3. Create scripts to update the backend database and validate in a test environment
- Python / SQL scripts were developed to perform the required sequential updates against the backend database.
- A standalone test environment was set up by:
  1. Copying the `P2000SQL` folder to a designated path.
  2. Reconfiguring the database connection in `PentagonDBConfig.xml` (located inside the `P2000SQL` folder).
<img width="1343" height="468" alt="Screenshot 2026-08-04 104125" src="https://github.com/user-attachments/assets/05bcadf9-f6d7-440c-8a4a-fd0fe31bd32f" />

- Ensure the database are modified as expected and the test environment frontend also gives expected output. 
