"""Read the source CSV and automatically navigate the complete project queue."""

import argparse
import json
from pathlib import Path
import sys
import time

from .browser import BrowserError
from .browser_auth import needs_sign_in
from .browser_migration import MigrationRunner, project_scope
from .browser_visual import install_upload_input_fallback
from .csv_config import validate_destination
from .journal import Journal
from .ocr import add_ocr_arguments, resolve_ocr
from .preflight import InputError, audit, atomic_json
from .runner import build_plan, exclusive_run, select_projects


def wait_for_sign_in(page, seconds):
    """Reuse the local browser session, otherwise wait only for authentication."""
    deadline = time.monotonic() + seconds
    announced = False
    while time.monotonic() < deadline:
        access = page.get_by_role("button", name="Enable accessibility", exact=True)
        if access.count() == 1 and access.is_visible():
            access.press("Enter")
        login = needs_sign_in(page)
        if not login and page.get_by_role("option", name="Open Project Finder", exact=True).count():
            return
        if not announced:
            print("Sign in in the migration Chrome window and dismiss any environment notice. Project and folder selection will run automatically.", flush=True)
            announced = True
        page.wait_for_timeout(500)
    raise BrowserError("Authentication did not finish before the sign-in timeout; no destination was selected")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-sets", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    add_ocr_arguments(parser)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--project-uid", action="append")
    parser.add_argument("--user-data-dir", type=Path, default=Path("state/browser-profile"))
    parser.add_argument("--channel", choices=("chromium", "chrome", "msedge"), default="chrome")
    parser.add_argument("--sign-in-timeout", type=int, default=600)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.sign_in_timeout <= 3600:
            raise InputError("Sign-in timeout must be between 1 and 3600 seconds")
        manifest = audit(args.version_sets, args.config)
        atomic_json(args.out / "manifest.json", manifest)
        atomic_json(args.out / "issues.json", manifest["issues"])
        plan = build_plan(manifest, args.project_uid)
        atomic_json(args.out / "run-plan.json", plan)
        projects = select_projects(manifest, args.project_uid)
        runnable = []
        for project in projects:
            if project["ready_for_upload"]:
                project_scope(manifest, project["project_uid"])
                runnable.append(project)
        if not args.execute:
            print(f"Plan saved to {args.out / 'run-plan.json'}; {len(runnable)} of {len(projects)} projects ready. No browser opened.")
            return 0 if len(runnable) == len(projects) else 2
        if not runnable:
            raise InputError("No selected project passed the source audit")
        environments = {p["target"]["environment"].rstrip("/") for p in runnable}
        domains = {p["target"]["domain_name"] for p in runnable}
        if len(environments) != 1 or len(domains) != 1:
            raise InputError("Use one Kahua environment and domain per queue invocation")
        ocr = resolve_ocr(args.ocr_backend, args.ocr_executable)
        ocr.check()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise InputError('Install the browser dependency: python -m pip install -e ".[browser]"') from None
        args.journal.parent.mkdir(parents=True, exist_ok=True)
        args.user_data_dir.mkdir(parents=True, exist_ok=True)
        with exclusive_run(args.journal), sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(args.user_data_dir.resolve()), channel=args.channel, headless=False,
                accept_downloads=True, viewport={"width": 1600, "height": 1000})
            try:
                install_upload_input_fallback(context)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(runnable[0]["target"]["environment"], timeout=60000)
                wait_for_sign_in(page, args.sign_in_timeout)
                journal = Journal(args.journal)
                try:
                    report = MigrationRunner(page, manifest, journal, args.out, ocr).run(args.project_uid)
                finally:
                    journal.close()
                print(json.dumps(report, indent=2))
                return 0 if report["passed"] else 2
            finally:
                context.close()
    except (InputError, BrowserError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Automatic migration stopped: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Automatic migration stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
