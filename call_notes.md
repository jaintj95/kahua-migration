# Project Overview: PlanGrid to Kahua Migration

- Migrating data from PlanGrid to Kahua for a client across ~1,000+ projects
- Each project has versioned document sets (e.g., Bid Set, Revision, Road Revision) with associated dates
- Files downloaded from PlanGrid as a zip, unzipped into a folder hierarchy reflecting version order
- Kahua will auto-compute the “current” version; no need to import the current folder directly
- Project and folder creation in Kahua already handled via API; automation scope is upload only

# Kahua Upload Workflow (Manual Steps to Automate)

- Navigate to project via 9-dot menu, search by project number, open via 45-degree arrow icon
- Open File Manager app, click into the Sheets folder
- Add files from the correct version folder, set batch name and date, apply revision number
- Select only files for the current batch (view must be filtered by batch name)
- Submit, accept, and release; then repeat for the next version folder in sequence
- Repeat the full process for every project

# Key Technical Challenge: Non-Standard UI

- Kahua uses a canvas-based UI (likely .NET/Silverlight-era tech), not standard HTML DOM
- Inspect element and standard browser automation selectors do not work out of the box
- Workaround found: enabling a semantic root layer exposes element IDs that automation can target
  - Element IDs are expected to be stable across projects for the migration window
  - K has partial browser automation working (login + some clicks) but mapping all elements is slow
- Desktop app exists but uses the same underlying technology; same challenge applies
- Decision pending: attempt web automation first; fall back to desktop if needed

# PlanGrid Download Script and Next Steps

- K has a working download script for PlanGrid, blocked only at login due to CAPTCHA detection
  - Login is a one-time step; once authenticated, all projects are accessible from one account
  - CAPTCHA appears only in the automated context, not in incognito; likely bot-detection triggered by script behavior
- K to generate two reference files from the database: project list (name + number) and version sets (batch dates + physical file paths)
- A test project (Seattle Heights or similar) exists in the dev environment for TJ to practice against
- All run outcomes to be logged: success/fail per version, skip rest of project on failure, move to next
