#!/usr/bin/env python3
"""Apply Ryu modernization patches with rollback support."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import importlib.metadata as metadata


BACKUP_EXT = ".ryu-modernize.bak"
STATE_FILE = ".ryu-modernize-state.json"
REPORT_JSON = "ryu_modernization_report.json"
REPORT_TXT = "ryu_modernization_report.txt"
COLLECTIONS_LOG = "collections_fix.log"

REQUIRED_PYTHON = (3, 8)

REQUIRED_DEPENDENCIES = {
    "eventlet": ">=0.33.3",
    "dnspython": ">=2.2.1",
    "greenlet": ">=1.1.0",
    "packaging": ">=20.9",
    "msgpack": ">=1.0.0",
    "netaddr": ">=0.8.0",
    "oslo.config": ">=5.2.0",
    "routes": ">=2.5.1",
    "webob": ">=1.8.7",
}

REQUIREMENTS_HEADER = "# Ryu modernization compatibility requirements"


@dataclass
class ChangeRecord:
    path: str
    description: str


def check_python_version() -> Optional[str]:
    if sys.version_info < REQUIRED_PYTHON:
        return (
            f"Python {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}+ required, "
            f"found {sys.version_info.major}.{sys.version_info.minor}"
        )
    return None


def parse_requirement(spec: str) -> "object":
    try:
        from packaging import version
    except ImportError:
        return None
    return version.parse(spec.lstrip("<>~=!="))


def check_dependencies() -> Dict[str, Dict[str, Optional[str]]]:
    results: Dict[str, Dict[str, Optional[str]]] = {}
    for name, requirement in REQUIRED_DEPENDENCIES.items():
        required_version = parse_requirement(requirement)
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            results[name] = {
                "required": requirement,
                "installed": None,
                "status": "missing",
            }
            continue

        if required_version is None:
            status = "unknown"
        else:
            from packaging import version

            installed_version = version.parse(installed)
            if installed_version < required_version:
                status = "outdated"
            else:
                status = "ok"
        results[name] = {
            "required": requirement,
            "installed": installed,
            "status": status,
        }
    return results


def load_state(state_path: Path) -> Dict[str, List[str]]:
    if not state_path.exists():
        return {"backups": [], "created_files": []}
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(state_path: Path, state: Dict[str, List[str]]) -> None:
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def ensure_backup(path: Path, state: Dict[str, List[str]]) -> None:
    backup_path = path.with_name(path.name + BACKUP_EXT)
    if backup_path.exists():
        if str(backup_path) not in state["backups"]:
            state["backups"].append(str(backup_path))
        return
    if not path.exists():
        return
    shutil.copy2(path, backup_path)
    state["backups"].append(str(backup_path))


def apply_collections_fix(repo_root: Path, state: Dict[str, List[str]]) -> Optional[Path]:
    log_path = repo_root / COLLECTIONS_LOG
    cmd = [
        sys.executable,
        str(repo_root / "tools" / "fix_collections_imports.py"),
        str(repo_root / "ryu"),
        "--backup-ext",
        BACKUP_EXT,
        "--log-file",
        str(log_path),
    ]
    subprocess.run(cmd, check=False)

    for backup in repo_root.rglob(f"*{BACKUP_EXT}"):
        backup_str = str(backup)
        if backup_str not in state["backups"]:
            state["backups"].append(backup_str)

    if log_path.exists() and str(log_path) not in state["created_files"]:
        if log_path.exists():
            state["created_files"].append(str(log_path))
        return log_path
    return log_path if log_path.exists() else None


def find_class_block(lines: List[str], class_name: str) -> Optional[tuple[int, int]]:
    for idx, line in enumerate(lines):
        if line.startswith(f"class {class_name}"):
            indent = len(line) - len(line.lstrip())
            end = idx + 1
            while end < len(lines):
                if lines[end].strip() == "":
                    end += 1
                    continue
                current_indent = len(lines[end]) - len(lines[end].lstrip())
                if current_indent <= indent:
                    break
                end += 1
            return idx, end
    return None


def ensure_eventlet_already_handled(path: Path, state: Dict[str, List[str]], changes: List[ChangeRecord]) -> None:
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    block = find_class_block(lines, "_AlreadyHandledResponse")
    if not block:
        return
    start, end = block
    block_text = "".join(lines[start:end])
    if "eventlet.wsgi" in block_text and "ALREADY_HANDLED" in block_text:
        return

    newline = "\n"
    if "\r\n" in content:
        newline = "\r\n"

    new_block = (
        "class _AlreadyHandledResponse(Response):" + newline
        "    # XXX: Eventlet API should not be used directly." + newline
        "    # https://github.com/benoitc/gunicorn/pull/2581" + newline
        "    from packaging import version" + newline
        "    import eventlet" + newline
        "    if version.parse(eventlet.__version__) >= version.parse(\"0.30.3\"):" + newline
        "        import eventlet.wsgi" + newline
        "        _ALREADY_HANDLED = getattr(eventlet.wsgi, \"ALREADY_HANDLED\", None)" + newline
        "    else:" + newline
        "        from eventlet.wsgi import ALREADY_HANDLED" + newline
        "        _ALREADY_HANDLED = ALREADY_HANDLED" + newline
        "" + newline
        "    def __call__(self, environ, start_response):" + newline
        "        return self._ALREADY_HANDLED" + newline
        newline
    )

    ensure_backup(path, state)
    new_content = "".join(lines[:start]) + new_block + "".join(lines[end:])
    path.write_text(new_content, encoding="utf-8")
    changes.append(ChangeRecord(str(path), "Updated _AlreadyHandledResponse for eventlet compatibility"))


def ensure_dnspython_compat(path: Path, state: Dict[str, List[str]], changes: List[ChangeRecord]) -> None:
    content = path.read_text(encoding="utf-8")
    if "dnspython_compat.patch_eventlet_dnspython()" in content:
        return

    target_line = "    import eventlet\n"
    if target_line not in content:
        return

    newline = "\n"
    if "\r\n" in content:
        newline = "\r\n"
        target_line = "    import eventlet\r\n"

    insertion = (
        target_line
        + "    from ryu.lib import dnspython_compat" + newline
        + "    dnspython_compat.patch_eventlet_dnspython()" + newline
    )

    ensure_backup(path, state)
    new_content = content.replace(target_line, insertion, 1)
    path.write_text(new_content, encoding="utf-8")
    changes.append(ChangeRecord(str(path), "Inserted dnspython tlsabase compatibility hook"))


def normalize_requirement_line(line: str) -> Optional[str]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    name = stripped.split(";")[0]
    for token in ["==", ">=", "<=", "~=", "<", ">"]:
        if token in name:
            return name.split(token, 1)[0].strip().lower()
    return name.strip().lower()


def update_requirements(path: Path, state: Dict[str, List[str]], changes: List[ChangeRecord]) -> None:
    required_lines = [
        REQUIREMENTS_HEADER,
        "",
    ] + [f"{name}{spec}" for name, spec in REQUIRED_DEPENDENCIES.items()]

    if path.exists():
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines()
    else:
        content = ""
        lines = []

    existing_names = {}
    updated_lines: List[str] = []
    for line in lines:
        name = normalize_requirement_line(line)
        if name and name in REQUIRED_DEPENDENCIES:
            updated_lines.append(f"{name}{REQUIRED_DEPENDENCIES[name]}")
            existing_names[name] = True
        else:
            updated_lines.append(line)

    missing = [name for name in REQUIRED_DEPENDENCIES if name not in existing_names]
    if missing:
        if updated_lines and updated_lines[-1].strip() != "":
            updated_lines.append("")
        updated_lines.extend(required_lines)

    new_content = "\n".join(updated_lines).rstrip() + "\n"
    if new_content == content:
        return

    if path.exists():
        ensure_backup(path, state)
    else:
        state["created_files"].append(str(path))

    path.write_text(new_content, encoding="utf-8")
    changes.append(ChangeRecord(str(path), "Updated requirements with modernization-compatible versions"))


def run_tests(repo_root: Path, command: List[str]) -> Dict[str, str]:
    result = {
        "command": " ".join(command),
        "status": "skipped",
        "output": "",
    }
    if not command:
        return result
    try:
        completed = subprocess.run(command, cwd=repo_root, check=False, capture_output=True, text=True)
        result["output"] = (completed.stdout + completed.stderr).strip()
        result["status"] = "passed" if completed.returncode == 0 else "failed"
    except OSError as exc:
        result["status"] = "error"
        result["output"] = str(exc)
    return result


def write_report(
    repo_root: Path,
    state: Dict[str, List[str]],
    changes: List[ChangeRecord],
    dependency_status: Dict[str, Dict[str, Optional[str]]],
    tests: Dict[str, str],
    python_issue: Optional[str],
) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    summary = {
        "python_ok": python_issue is None,
        "dependencies_ok": all(
            entry["status"] == "ok" for entry in dependency_status.values()
        ),
        "patches_applied": bool(changes),
        "tests_status": tests.get("status", "skipped"),
    }
    report = {
        "timestamp": timestamp,
        "python": {
            "version": sys.version.split()[0],
            "issue": python_issue,
        },
        "dependencies": dependency_status,
        "changes": [change.__dict__ for change in changes],
        "tests": tests,
        "summary": summary,
    }

    report_json_path = repo_root / REPORT_JSON
    report_txt_path = repo_root / REPORT_TXT

    if report_json_path.exists():
        ensure_backup(report_json_path, state)
    else:
        state["created_files"].append(str(report_json_path))

    if report_txt_path.exists():
        ensure_backup(report_txt_path, state)
    else:
        state["created_files"].append(str(report_txt_path))

    report_json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        f"Ryu modernization report ({timestamp})",
        "",
        f"Python version: {report['python']['version']}",
    ]
    if python_issue:
        lines.append(f"Python issue: {python_issue}")
    lines.append("")
    lines.append("Dependency status:")
    for name, details in dependency_status.items():
        lines.append(
            f"- {name}: {details['status']} (installed={details['installed']}, required={details['required']})"
        )
    lines.append("")
    lines.append("Changes applied:")
    if changes:
        for change in changes:
            lines.append(f"- {change.path}: {change.description}")
    else:
        lines.append("- No changes needed.")
    lines.append("")
    lines.append("Tests:")
    lines.append(f"- {tests.get('status', 'skipped')}: {tests.get('command', '')}")
    if tests.get("output"):
        lines.append("Output:")
        lines.append(tests["output"])
    lines.append("")
    lines.append("Compatibility summary:")
    for key, value in summary.items():
        lines.append(f"- {key}: {value}")

    report_txt_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def rollback(repo_root: Path) -> int:
    state_path = repo_root / STATE_FILE
    if not state_path.exists():
        print("No modernization state found; nothing to rollback.")
        return 0

    state = load_state(state_path)
    for backup_str in state.get("backups", []):
        backup_path = Path(backup_str)
        if not backup_path.exists():
            continue
        original = backup_path.with_name(backup_path.name[: -len(BACKUP_EXT)])
        shutil.copy2(backup_path, original)
        backup_path.unlink()

    for created in state.get("created_files", []):
        created_path = Path(created)
        if created_path.exists():
            created_path.unlink()

    state_path.unlink()
    print("Rollback completed.")
    return 0


def apply(repo_root: Path, test_command: Optional[str], skip_tests: bool) -> int:
    state_path = repo_root / STATE_FILE
    state = load_state(state_path)
    changes: List[ChangeRecord] = []

    python_issue = check_python_version()
    dependency_status = check_dependencies()

    log_path = apply_collections_fix(repo_root, state)
    if log_path:
        changes.append(ChangeRecord(str(log_path), "Collections import fix log generated"))

    ensure_eventlet_already_handled(
        repo_root / "ryu" / "app" / "wsgi.py",
        state,
        changes,
    )
    ensure_dnspython_compat(
        repo_root / "ryu" / "lib" / "hub.py",
        state,
        changes,
    )
    update_requirements(repo_root / "requirements.txt", state, changes)

    tests = {"status": "skipped", "command": "", "output": ""}
    if not skip_tests:
        if test_command:
            tests = run_tests(repo_root, test_command.split())
        else:
            tests = run_tests(repo_root, ["./run_tests.sh"])

    write_report(repo_root, state, changes, dependency_status, tests, python_issue)
    save_state(state_path, state)

    print("Modernization complete. Report written to:")
    print(f"- {repo_root / REPORT_JSON}")
    print(f"- {repo_root / REPORT_TXT}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ryu modernization patch helper")
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="Rollback to the original state using backups",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip running tests",
    )
    parser.add_argument(
        "--tests",
        default=None,
        help="Override test command (default: ./run_tests.sh)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parent.parent

    if args.rollback:
        return rollback(repo_root)
    return apply(repo_root, args.tests, args.skip_tests)


if __name__ == "__main__":
    raise SystemExit(main())
