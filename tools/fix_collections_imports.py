#!/usr/bin/env python3
"""Fix deprecated collections imports/usages for Python 3.10+ compatibility."""
from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple

FROM_COLLECTIONS_RE = re.compile(r"^(?P<indent>\s*)from\s+collections\s+import\s+(?P<imports>.+)$")
IMPORT_COLLECTIONS_RE = re.compile(r"^\s*import\s+(?P<imports>.+)$")
COLLECTIONS_ABC_IMPORT_RE = re.compile(
    r"^\s*(import\s+collections\.abc\b|from\s+collections\.abc\s+import\b|from\s+collections\s+import\s+abc\b)"
)
COLLECTIONS_IMPORT_RE = re.compile(r"^\s*(import\s+collections\b|from\s+collections\s+import\b)")

REPLACEMENTS = {
    re.compile(r"\bcollections\.MutableMapping\b"): "collections.abc.MutableMapping",
    re.compile(r"\bcollections\.Iterable\b"): "collections.abc.Iterable",
    re.compile(r"\bcollections\.Callable\b"): "collections.abc.Callable",
}


@dataclass
class Change:
    line_no: int
    description: str


@dataclass
class FileResult:
    path: Path
    changes: List[Change]
    updated: bool


def split_comment(line: str) -> Tuple[str, str]:
    if "#" not in line:
        return line, ""
    code, comment = line.split("#", 1)
    return code, f"#{comment}"


def parse_import_items(imports_part: str) -> List[str]:
    imports_part = imports_part.strip()
    if imports_part.startswith("(") and imports_part.endswith(")"):
        imports_part = imports_part[1:-1]
    return [item.strip() for item in imports_part.split(",") if item.strip()]


def base_import_name(item: str) -> str:
    if " as " in item:
        return item.split(" as ", 1)[0].strip()
    return item.strip()


def process_from_collections_import(line: str, line_no: int) -> Tuple[List[str], List[Change]]:
    changes: List[Change] = []
    line_no_ending = line.rstrip("\r\n")
    match = FROM_COLLECTIONS_RE.match(line_no_ending)
    if not match:
        return [line], changes

    line_ending = ""
    if line.endswith("\r\n"):
        line_ending = "\r\n"
    elif line.endswith("\n"):
        line_ending = "\n"

    indent = match.group("indent")
    imports_part = match.group("imports")
    code, comment = split_comment(imports_part)
    items = parse_import_items(code)

    if not items or "*" in items:
        return [line], changes

    mutable_items = [item for item in items if base_import_name(item) == "MutableMapping"]
    if not mutable_items:
        return [line], changes

    remaining_items = [item for item in items if base_import_name(item) != "MutableMapping"]
    new_lines: List[str] = []

    if remaining_items:
        remaining = ", ".join(remaining_items)
        new_lines.append(f"{indent}from collections import {remaining}{comment}{line_ending}")
        changes.append(Change(line_no, "removed MutableMapping from collections import"))
        mutable_comment = ""
    else:
        changes.append(Change(line_no, "replaced collections import with collections.abc for MutableMapping"))
        mutable_comment = comment

    mutable_imports = ", ".join(mutable_items)
    new_lines.append(f"{indent}from collections.abc import {mutable_imports}{mutable_comment}{line_ending}")

    return new_lines, changes


def replace_collections_usage(line: str, line_no: int) -> Tuple[str, List[Change]]:
    changes: List[Change] = []
    new_line = line
    for pattern, replacement in REPLACEMENTS.items():
        if pattern.search(new_line):
            new_line = pattern.sub(replacement, new_line)
            changes.append(Change(line_no, f"replaced {pattern.pattern} with {replacement}"))
    return new_line, changes


def has_collections_abc_import(lines: Iterable[str]) -> bool:
    return any(COLLECTIONS_ABC_IMPORT_RE.match(line) for line in lines)


def has_collections_import(lines: Iterable[str]) -> bool:
    for line in lines:
        if not COLLECTIONS_IMPORT_RE.match(line):
            continue
        if line.lstrip().startswith("import "):
            match = IMPORT_COLLECTIONS_RE.match(line)
            if not match:
                return True
            code, _ = split_comment(match.group("imports"))
            items = parse_import_items(code)
            if any(base_import_name(item).startswith("collections") for item in items):
                return True
        else:
            return True
    return False


def insert_collections_abc_import(lines: List[str], newline: str, changes: List[Change]) -> None:
    insert_at = None
    for idx, line in enumerate(lines):
        if COLLECTIONS_IMPORT_RE.match(line):
            insert_at = idx + 1
    if insert_at is None:
        return
    lines.insert(insert_at, f"import collections.abc{newline}")
    changes.append(Change(insert_at + 1, "added import collections.abc"))


def backup_path(path: Path, backup_ext: str) -> Path:
    candidate = path.with_name(path.name + backup_ext)
    if not candidate.exists():
        return candidate
    counter = 1
    while True:
        candidate = path.with_name(f"{path.name}{backup_ext}.{counter}")
        if not candidate.exists():
            return candidate
        counter += 1


def process_file(path: Path, dry_run: bool, backup_ext: str, logger: logging.Logger) -> FileResult:
    changes: List[Change] = []
    try:
        with tokenize.open(path) as handle:
            encoding = handle.encoding or "utf-8"
            content = handle.read()
    except (SyntaxError, UnicodeDecodeError, OSError) as exc:
        logger.warning("Skipping %s due to read error: %s", path, exc)
        return FileResult(path, [], False)

    newline = "\r\n" if "\r\n" in content else "\n"
    lines = content.splitlines(keepends=True)

    collections_imported = has_collections_import(lines)
    collections_abc_imported = has_collections_abc_import(lines)

    updated_lines: List[str] = []
    for idx, line in enumerate(lines, start=1):
        new_lines, import_changes = process_from_collections_import(line, idx)
        if import_changes:
            changes.extend(import_changes)
        for new_line in new_lines:
            replaced_line, line_changes = replace_collections_usage(new_line, idx)
            updated_lines.append(replaced_line)
            changes.extend(line_changes)

    if collections_imported and not collections_abc_imported:
        if not has_collections_abc_import(updated_lines):
            insert_collections_abc_import(updated_lines, newline, changes)

    updated_content = "".join(updated_lines)
    if updated_content == content:
        return FileResult(path, [], False)

    if dry_run:
        logger.info("[dry-run] Would update %s", path)
    else:
        backup = backup_path(path, backup_ext)
        shutil.copy2(path, backup)
        with open(path, "w", encoding=encoding, newline="") as handle:
            handle.write(updated_content)
        logger.info("Updated %s (backup: %s)", path, backup)

    for change in changes:
        logger.info("%s:%s %s", path, change.line_no, change.description)

    return FileResult(path, changes, True)


def iter_py_files(root: Path, backup_ext: str) -> Iterable[Path]:
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            if filename.endswith(backup_ext):
                continue
            yield Path(dirpath) / filename


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fix deprecated collections imports/usages for Python 3.10+ compatibility.",
    )
    parser.add_argument("path", nargs="?", default=".", help="Directory to scan")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying files",
    )
    parser.add_argument(
        "--backup-ext",
        default=".bak",
        help="Extension to use for backup files (default: .bak)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Optional log file path",
    )
    return parser


def setup_logger(log_file: str | None) -> logging.Logger:
    logger = logging.getLogger("collections-fixer")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(levelname)s: %(message)s")

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    logger = setup_logger(args.log_file)
    root = Path(args.path).resolve()

    if not root.exists():
        logger.error("Path does not exist: %s", root)
        return 2

    results: List[FileResult] = []
    for path in iter_py_files(root, args.backup_ext):
        results.append(process_file(path, args.dry_run, args.backup_ext, logger))

    updated_files = [result for result in results if result.updated]
    logger.info("Processed %s files, updated %s", len(results), len(updated_files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
