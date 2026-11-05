"""First-run local settings for the drawings browser command."""

import json
from pathlib import Path
import sys

from .csv_config import validate_destination
from .preflight import InputError, inside


DEFAULT_ENVIRONMENT = "https://launch.kahua.com/"
DEFAULT_DOMAIN = "TDOT.ECPCS"


def default_config():
    return {
        "schema_version": 1,
        "source_root": "drawings",
        "revision_policy": "per_sheet_occurrence",
        "revision_start": 1,
        "project_defaults": {
            "drawing_log": "drawing_log.csv",
            "current_log": "current_set_log.csv",
            "current_folder": "Drawings/Current",
            "target": {
                "transport": "browser",
                "environment": DEFAULT_ENVIRONMENT,
                "domain_name": DEFAULT_DOMAIN,
                "folder_path": "Sheets",
                "project_policy": "find_or_create",
                "folder_policy": "find_or_create",
                "record_type": "Project",
            },
        },
    }


def ensure_config(path, *, no_input=False):
    """Create a missing settings file once; never replace an existing file."""
    path = Path(path).expanduser()
    if path.exists():
        if not path.is_file():
            raise InputError(f"Configuration path is not a file: {path}")
        return path

    config = default_config()
    defaults = config["project_defaults"]
    target = defaults["target"]
    interactive = not no_input and getattr(sys.stdin, "isatty", lambda: False)()
    print("First-run Drawings setup.", flush=True)
    if interactive:
        print("Press Enter to keep each default.", flush=True)
    print("Project-specific settings in the CSV take precedence over these defaults.", flush=True)
    if not interactive:
        print("No interactive input requested or available; using the defaults.", flush=True)

    def ask(label, default, validate):
        nonlocal interactive
        while True:
            value = default
            if interactive:
                try:
                    value = input(f"{label} [{default}]: ").strip() or default
                except EOFError:
                    interactive = False
                    print("Using defaults for the remaining settings.", flush=True)
                except KeyboardInterrupt:
                    raise InputError("Setup cancelled; no configuration was saved") from None
            try:
                validate(value)
                return value
            except InputError as exc:
                if not interactive:
                    raise
                print(str(exc), flush=True)

    def target_value(field):
        def validate(value):
            # Shared defaults do not select a project. These temporary names
            # satisfy the full destination validator and are never saved.
            validate_destination({**target, field: value,
                                  "project_number": "setup-check", "project_name": "setup-check"})
        return validate

    def yes_no(value):
        if value.casefold() not in ("yes", "y", "no", "n"):
            raise InputError("Enter yes or no, or press Enter to keep the default")

    for field, label in (("environment", "Kahua environment URL"),
                         ("domain_name", "Kahua domain"),
                         ("folder_path", "Destination folder")):
        target[field] = ask(label, target[field], target_value(field))
    for field, label in (("project_policy", "Create missing projects? (yes/no)"),
                         ("folder_policy", "Create missing folders? (yes/no)")):
        answer = ask(label, "yes", yes_no)
        target[field] = "find_or_create" if answer.casefold() in ("yes", "y") else "existing_only"
    for field, label in (("drawing_log", "Default drawing log filename"),
                         ("current_log", "Default current-set log filename")):
        defaults[field] = ask(label, defaults[field], lambda value: inside(Path.cwd(), value))

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except FileExistsError:
        # Another invocation may have finished setup while this one prompted.
        print(f"Configuration already exists; reusing {path.resolve()}", flush=True)
    else:
        print(f"Saved Drawings settings to {path.resolve()}", flush=True)
        print(f"Environment: {target['environment']}\nDomain: {target['domain_name']}\n"
              f"Destination folder: {target['folder_path']}", flush=True)
    return path
