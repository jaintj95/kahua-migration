## Task: migrate documents and photos into Kahua

Build a repeatable way to upload the **documents** and **photos** from downloaded project ZIPs into their corresponding Kahua projects. The existing drawings work is separate. Doe is handling project creation and ZIP downloads; this task starts once the Kahua project exists and its ZIP is available. The eventual goal is to run the process for **100–200 projects in batches**, using a CSV whose project numbers and names must match the Kahua projects.

### Proposed file-sync workflow

1. **Select the existing project in the Kahua desktop app.** In project settings, open **Local Files** and enable local sync. Kahua should create a project-specific directory on the Windows VM. During the demo, the expected *Enable* button did not appear, so the exact UI action still needs to be verified.
2. **Extract the downloaded ZIP.** Copy—not move—the **documents** and **photos** folders into that project’s local sync directory. Kahua should then upload their contents automatically. A basic script can first be tested against a single project before adding CSV-driven batch handling.
3. **Verify the upload, then disable sync before cleanup.** Deleting local files while sync remains active could also delete them from Kahua. Vikas specifically wanted disabling sync included as a safety measure before anyone clears VM storage or reuses the workflow.

For batch use, Doe also needs to prepare and sanity-check the CSV so each ZIP’s project number and name map to the right Kahua project. That testing was described as work to do once the script is ready, not as something already complete.

## Possibilities considered

- **Direct API from a local Python script — preferred first test.** asked Doe to obtain sandbox API credentials so he could try a few files and check whether uploads work, whether Kahua imposes rate limits or other thresholds, and whether the necessary endpoints exist. If that works, it may avoid desktop automation. It was a proposed test, **not an established solution**.
- **Existing API route through Snaplogic.** Doe understood that numerous API calls, possible middleware overload and possibly unavailable endpoints had made this route unattractive. He explicitly said he did **not** know the exact reason the API approach had been ruled out. He did not identify a security prohibition on making direct API calls.
- **Desktop automation on the Windows VM — fallback if the API test fails.** Automate enabling sync in Kahua, copying the extracted folders, checking the result and disabling sync. Doe noted this needs desktop-app automation rather than the browser automation used for earlier work; no particular framework was chosen. The OnIndus VM can be scaled up if its free-tier resources cause lag.
- **Manual upload per project.** K mentioned going into each project and uploading files individually, but presented local file sync as the alternative for handling many projects.
