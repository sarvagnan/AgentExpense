#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pypdf[crypto]>=6.16,<7",
#   "pyobjc-framework-Cocoa>=12,<13",
# ]
# ///

"""Unlock selected PDFs and atomically replace the originals."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApplication,
    NSMakeRect,
    NSSecureTextField,
)
from pypdf import PdfReader, PdfWriter


@dataclass(frozen=True)
class Result:
    path: Path
    status: str
    detail: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unlock PDFs in place using one password.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="verify that each PDF can be opened without changing it",
    )
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("pdfs", nargs="+", help="PDF files to process")
    return parser.parse_args()


def normalize_path(raw_path: str) -> Path:
    if raw_path.startswith("file:"):
        parsed = urlparse(raw_path)
        raw_path = unquote(parsed.path)
    return Path(raw_path).expanduser().absolute()


def selected_pdfs(raw_paths: list[str]) -> list[Path]:
    selected: list[Path] = []
    seen: set[Path] = set()

    for raw_path in raw_paths:
        path = normalize_path(raw_path)
        if path.suffix.lower() != ".pdf" or path in seen:
            continue
        seen.add(path)
        selected.append(path)

    return selected


def secure_password_prompt(pdf_count: int) -> str | None:
    application = NSApplication.sharedApplication()
    application.activateIgnoringOtherApps_(True)

    alert = NSAlert.alloc().init()
    alert.setMessageText_("Unlock PDFs in place")
    alert.setInformativeText_(
        f"This will permanently replace {pdf_count} selected PDF(s) with "
        "validated, unlocked copies. No backup will be retained."
    )
    alert.addButtonWithTitle_("Unlock and Replace")
    alert.addButtonWithTitle_("Cancel")

    password_field = NSSecureTextField.alloc().initWithFrame_(
        NSMakeRect(0, 0, 340, 24)
    )
    password_field.setPlaceholderString_("PDF password")
    alert.setAccessoryView_(password_field)

    if alert.runModal() != NSAlertFirstButtonReturn:
        return None
    return str(password_field.stringValue())


def show_summary(results: list[Result]) -> None:
    unlocked = sum(result.status == "unlocked" for result in results)
    skipped = sum(result.status == "skipped" for result in results)
    failed = [result for result in results if result.status == "failed"]

    summary = (
        f"{unlocked} unlocked and replaced; {skipped} already unlocked; "
        f"{len(failed)} failed."
    )
    if failed:
        failure_lines = [
            f"{result.path.name} ({result.detail})" for result in failed
        ]
        summary += "\n\n" + "\n".join(failure_lines)

    application = NSApplication.sharedApplication()
    application.activateIgnoringOtherApps_(True)
    alert = NSAlert.alloc().init()
    alert.setMessageText_("Unlock PDFs")
    alert.setInformativeText_(summary)
    alert.addButtonWithTitle_("OK")
    alert.runModal()


def copy_extended_attributes(source: Path, destination: Path) -> None:
    if not all(
        hasattr(os, attribute)
        for attribute in ("listxattr", "getxattr", "setxattr")
    ):
        return

    try:
        attribute_names = os.listxattr(source, follow_symlinks=False)
    except OSError:
        return

    for attribute_name in attribute_names:
        try:
            value = os.getxattr(
                source,
                attribute_name,
                follow_symlinks=False,
            )
            os.setxattr(
                destination,
                attribute_name,
                value,
                follow_symlinks=False,
            )
        except OSError:
            # A nonessential Finder attribute should not block PDF recovery.
            continue


def unlock_pdf(path: Path, password: str) -> Result:
    if path.is_symlink():
        return Result(path, "failed", "symbolic links are not supported")
    if not path.is_file():
        return Result(path, "failed", "file does not exist")

    temporary_path: Path | None = None
    try:
        reader = PdfReader(path, strict=False)
        if not reader.is_encrypted:
            return Result(path, "skipped")
        if not reader.decrypt(password):
            return Result(path, "failed", "incorrect password")

        original_page_count = len(reader.pages)
        original_stat = path.stat()
        writer = PdfWriter(clone_from=reader)

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.unlocking-",
            suffix=".pdf",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(file_descriptor, "wb") as temporary_file:
            writer.write(temporary_file)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        validation_reader = PdfReader(temporary_path, strict=False)
        if validation_reader.is_encrypted:
            raise ValueError("validation found remaining encryption")
        if len(validation_reader.pages) != original_page_count:
            raise ValueError("validation found a page-count mismatch")

        os.chmod(temporary_path, stat.S_IMODE(original_stat.st_mode))
        shutil.copystat(path, temporary_path, follow_symlinks=False)
        copy_extended_attributes(path, temporary_path)
        os.replace(temporary_path, path)
        temporary_path = None

        final_reader = PdfReader(path, strict=False)
        if final_reader.is_encrypted or len(final_reader.pages) != original_page_count:
            raise ValueError("post-replacement verification failed")

        return Result(path, "unlocked")
    except Exception as error:  # Keep one bad statement from stopping the batch.
        return Result(path, "failed", str(error))
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def check_pdf(path: Path) -> Result:
    if path.is_symlink():
        return Result(path, "failed", "symbolic links are not supported")
    if not path.is_file():
        return Result(path, "failed", "file does not exist")

    try:
        reader = PdfReader(path, strict=False)
        encryption = "encrypted" if reader.is_encrypted else "unlocked"
        return Result(path, "checked", encryption)
    except Exception as error:
        return Result(path, "failed", str(error))


def main() -> int:
    args = parse_args()
    pdfs = selected_pdfs(args.pdfs)
    if not pdfs:
        print("No PDF files were supplied.", file=sys.stderr)
        return 2

    if args.check_only:
        results = [check_pdf(path) for path in pdfs]
        for result in results:
            print(f"{result.status.upper()}: {result.path.name} ({result.detail})")
        return 1 if any(result.status == "failed" for result in results) else 0

    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = secure_password_prompt(len(pdfs))
        if password is None:
            return 0

    results = [unlock_pdf(path, password) for path in pdfs]
    if not args.password_stdin:
        show_summary(results)
    for result in results:
        detail = f" ({result.detail})" if result.detail else ""
        print(f"{result.status.upper()}: {result.path}{detail}")
    return 1 if any(result.status == "failed" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
