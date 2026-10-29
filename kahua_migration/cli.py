"""Command line entry points. Plans need no credentials and make no network calls."""

import argparse
import json
import os
import sys
from pathlib import Path

from .contract import validate_contract
from .http_client import ApiError, KahuaClient
from .journal import Journal
from .kahua import KahuaAdapter
from .preflight import InputError, atomic_json, main as preflight_main
from .runner import Runner, build_plan, exclusive_run


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("audit", "reconcile"):
        return preflight_main(argv)
    if argv and argv[0] == "browser":
        from .browser_cli import main as browser_main
        return browser_main(argv[1:])
    parser = argparse.ArgumentParser(description="PlanGrid to Kahua migration: audit, plan, run, reconcile")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "api-run"):
        command = sub.add_parser(name, help="Write a local run plan" if name == "plan" else "Execute a verified migration")
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--project-uid", action="append")
        command.add_argument("--out", type=Path, required=True)
        if name == "api-run":
            command.add_argument("--contract", type=Path, required=True)
            command.add_argument("--journal", type=Path, required=True)
            command.add_argument("--execute", action="store_true", help="Permit target writes after all validations pass")
    sub.add_parser("audit", help="Validate CSVs, files, revision rules and mappings (use audit --help)")
    sub.add_parser("reconcile", help="Compare a complete normalized target snapshot (use reconcile --help)")
    sub.add_parser("browser", help="Browser login, inspect and run commands (use browser --help)")
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        plan = build_plan(manifest, args.project_uid)
        atomic_json(args.out / "run-plan.json", plan)
        if args.command == "plan":
            print(f"Saved {args.out / 'run-plan.json'}; no network requests were made.")
            return 0
        if not args.execute:
            raise InputError("Plan saved. API uploads require api-run --execute and a verified tenant contract.")
        contract = json.loads(args.contract.read_text(encoding="utf-8"))
        validate_contract(contract)
        required = ("KAHUA_CLIENT_ID", "KAHUA_CLIENT_SECRET")
        if any(not os.environ.get(name) for name in required):
            raise InputError("Set KAHUA_CLIENT_ID and KAHUA_CLIENT_SECRET in the local environment")
        args.journal.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_run(args.journal):
            client = KahuaClient(contract["service_origin"], os.environ[required[0]], os.environ[required[1]])
            adapter = KahuaAdapter(client, contract)
            journal = Journal(args.journal)
            try:
                report = Runner(manifest, adapter, journal, args.out).run(args.project_uid)
            finally:
                journal.close()
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] else 2
    except (InputError, ApiError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Migration stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
