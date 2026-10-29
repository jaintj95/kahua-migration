"""Local headed-browser worker. Login and live calibration are explicit operator steps."""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .browser import BrowserAdapter, BrowserError, validate_browser_profile
from .journal import Journal
from .preflight import InputError, atomic_json
from .runner import Runner, build_plan, exclusive_run


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "migrate":
        from .migration_cli import main as migrate_main
        return migrate_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="Read CSV, find/create destinations, and process the project queue")
    for name in ("login", "inspect", "audit", "run", "pilot"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--profile", type=Path, default=Path("browser-profile.example.json"))
        cmd.add_argument("--user-data-dir", type=Path, default=Path("state/browser-profile"))
        cmd.add_argument("--out", type=Path, default=Path("output/browser"))
        cmd.add_argument("--channel", choices=("chromium", "chrome", "msedge"), default="chromium")
        if name in ("audit", "pilot"):
            cmd.add_argument("--manifest", type=Path, required=True)
            cmd.add_argument("--project-uid")
            cmd.add_argument("--ocr-executable", type=Path, required=True,
                             help="Compiled local macOS screenshot OCR helper")
        if name == "pilot":
            cmd.add_argument("--journal", type=Path, required=True)
            cmd.add_argument("--execute", action="store_true",
                             help="Run the bounded pilot after manual sign-in and exact folder selection")
        if name == "run":
            cmd.add_argument("--manifest", type=Path, required=True)
            cmd.add_argument("--project-uid", action="append")
            cmd.add_argument("--journal", type=Path, required=True)
            cmd.add_argument("--manual-files", action="store_true", help="Let the operator choose each file; never operate Finder")
            cmd.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        profile = json.loads(args.profile.read_text(encoding="utf-8"))
        url = urlsplit(profile.get("environment", ""))
        if (url.scheme != "https" or not url.hostname or not url.hostname.endswith(".kahua.com")
                or url.username or url.password or url.query or url.fragment):
            raise InputError("Browser profile must identify a Kahua HTTPS environment without credentials")
        args.out.mkdir(parents=True, exist_ok=True)
        if args.command in ("run", "pilot"):
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            selected = args.project_uid if args.command == "run" else ([args.project_uid] if args.project_uid else None)
            atomic_json(args.out / "run-plan.json", build_plan(manifest, selected))
            if args.command == "pilot":
                from .browser_pilot import pilot_scope
                project, _, _ = pilot_scope(manifest, args.project_uid)
                if project["target"]["environment"].rstrip("/") != profile["environment"].rstrip("/"):
                    raise InputError("The browser profile and pilot destination use different environments")
            if not args.execute:
                print(f"Plan saved in {args.out / 'run-plan.json'}. No browser session or upload was started.")
                return 0
            if args.command == "run":
                validate_browser_profile(profile)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise InputError("Install the browser dependency: pip install -e '.[browser]' then python -m playwright install chromium") from None
        args.user_data_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(args.user_data_dir.resolve()), channel=args.channel, headless=False, accept_downloads=True,
                viewport=profile.get("viewport", {"width": 1600, "height": 1000}))
            try:
                fallback = args.command == "pilot" or profile.get("upload_picker") == "html_input_fallback"
                if fallback:
                    from .browser_visual import install_upload_input_fallback
                    install_upload_input_fallback(context)
                page = context.pages[0] if context.pages else context.new_page()
                if fallback or not page.url.startswith(profile["environment"].rstrip("/")):
                    page.goto(profile["environment"], timeout=60000)
                if args.command in ("login", "inspect", "audit", "pilot"):
                    print("Sign in manually in this browser window and open the intended project/folder. The script does not enter credentials.")
                    input("Press Enter here when File Manager is available: ")
                    access = page.get_by_role("button", name="Enable accessibility", exact=True)
                    if access.count() == 1 and access.is_visible():
                        access.press("Enter", timeout=30000)
                    if args.command in ("inspect", "audit", "pilot"):
                        # Do not save a body snapshot while sign-in is visible:
                        # this tenant's accessibility layer can expose passwords.
                        page.get_by_role("main", name="File Manager Main Content", exact=True).wait_for(
                            state="visible", timeout=30000)
                        page.get_by_role("region", name="Record List", exact=True).wait_for(
                            state="visible", timeout=30000)
                    if args.command == "pilot":
                        from .browser_pilot import PilotRunner
                        with exclusive_run(args.journal):
                            journal = Journal(args.journal)
                            try:
                                report = PilotRunner(page, manifest, journal, args.out,
                                                     args.ocr_executable, args.project_uid).run()
                            finally:
                                journal.close()
                        print(json.dumps(report, indent=2))
                        return 0 if report["passed"] else 2
                    if args.command == "audit":
                        from .browser_readback import ReadOnlyInspector, compare_readback
                        from .runner import select_projects
                        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
                        projects = select_projects(manifest, [args.project_uid] if args.project_uid else None)
                        if len(projects) != 1:
                            raise InputError("Select exactly one project for the read-only calibration audit")
                        project = projects[0]
                        report = ReadOnlyInspector(page, project["target"], args.out,
                                                   args.ocr_executable).read()
                        records = [r for r in manifest["files"] if r["project_uid"] == project["project_uid"]]
                        comparison = compare_readback(report, records)
                        atomic_json(args.out / "metadata-audit.json", comparison)
                        print(json.dumps(comparison, indent=2))
                        return 0 if comparison["passed"] else 2
                    if args.command == "inspect":
                        if page.get_by_role("textbox", name="Password", exact=True).count():
                            raise BrowserError("Sign-in controls remain present; refusing to capture their values")
                        (args.out / "accessible-page.txt").write_text(page.locator("body").aria_snapshot(), encoding="utf-8")
                        page.screenshot(path=str(args.out / "page.png"))
                        print(f"Saved inspection evidence in {args.out}. No drawing records were changed.")
                    else:
                        print(f"Browser session saved locally in {args.user_data_dir}.")
                    return 0
                with exclusive_run(args.journal):
                    adapter = BrowserAdapter(page, profile, args.out / "browser-evidence", manual_files=args.manual_files)
                    journal = Journal(args.journal)
                    try:
                        report = Runner(manifest, adapter, journal, args.out).run(args.project_uid)
                    finally:
                        journal.close()
                print(json.dumps(report, indent=2))
                return 0 if report["passed"] else 2
            finally:
                context.close()
    except (InputError, BrowserError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Browser migration stopped: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Browser session stopped: {type(exc).__name__}. Check the browser session and saved evidence before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
