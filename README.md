# Team Daily Planner — local web prototype

This is a local Flask workspace for the existing `Team Daily Planner.xlsx` file. The Excel file remains the source of truth:

- Adding a member writes to the workbook's **Team** sheet.
- Adding or updating a task writes to the **Tasks** sheet immediately.
- The first change also creates an **Activity log** sheet in the same workbook, so updates are traceable in Excel.
- The manager dashboard polls every five seconds for updates and can show browser alerts. Teams/email delivery is intentionally not connected yet.

## Run it locally

From this folder, run:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/flask --app app run --debug
```

Then open http://127.0.0.1:5000 in a browser.

The app keeps a one-time backup at `Team Daily Planner.backup.xlsx` before its first change. Do not edit the local workbook in Excel at the same time as the prototype is saving it. Refresh the browser after manual Excel edits to view them.

## Suggested first check

1. Add a few team members on **Team**.
2. Add a next-day task as a team member.
3. Open a second browser window on **Manager dashboard**.
4. Update the task’s status and 7 PM note in the first window.
5. Within five seconds, the manager window should show a live update; the workbook should show the changed task and an Activity log sheet.

## Later integration

Once this workflow is approved, Teams/email delivery should be added with authenticated Microsoft 365 credentials and a production-safe workbook store (SharePoint/OneDrive with Microsoft Graph or Power Automate). This prototype deliberately sends no external messages and does not include user login/permissions.
