"""
Forget stored documents so the next run re-extracts them.

The pipeline skips any document whose text hash matches what's stored,
which is what keeps re-runs cheap - but it also means a prompt change, a
model switch, or a code change produces no visible difference until the
stored row is removed. This is the tool for that.

Usage:
    python -m src.utils.reset_documents                 # list what's stored
    python -m src.utils.reset_documents ACME            # forget matches for "ACME"
    python -m src.utils.reset_documents a.pdf b.pdf     # forget several
    python -m src.utils.reset_documents --failed        # forget failed extractions
    python -m src.utils.reset_documents --all           # forget everything
    python -m src.utils.reset_documents --all --yes     # ... without confirming
"""

import argparse
import os
import sys
from pathlib import Path

# Allow running as a script (python src/utils/reset_documents.py) as well as
# a module, by making the project root importable either way.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.storage_config import load_storage_config
from src.storage.contract_repository import SqliteContractRepository
from src.storage.database import Database


def _match(stored_paths, term):
    """
    Resolve a user-supplied term to stored document paths.

    Accepts a real path in any spelling (resolved the same way the pipeline
    canonicalizes it before storing), otherwise falls back to a
    case-insensitive substring match on the path - so "ACME" or "acme order"
    finds the document without typing the whole thing.

    Args:
        stored_paths: Canonical paths currently in the database.
        term: What the user typed.

    Returns:
        list: Matching stored paths.
    """
    if os.path.exists(term):
        canonical = str(Path(term).resolve())
        if canonical in stored_paths:
            return [canonical]

    lowered = term.lower()
    return [p for p in stored_paths if lowered in p.lower()]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="reset_documents",
        description="Forget stored documents so the next run re-extracts them.",
    )
    parser.add_argument(
        "documents", nargs="*",
        help="Path or name fragment of the document(s) to forget. "
             "With none, lists what's stored.")
    parser.add_argument("--all", action="store_true", help="Forget every document.")
    parser.add_argument("--failed", action="store_true",
                        help="Forget only documents whose extraction failed.")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip the confirmation prompt for --all.")
    args = parser.parse_args(argv)

    database = Database(load_storage_config().database_path)
    repository = SqliteContractRepository(database)
    stored = repository.fetch_all()

    if not stored:
        print("No documents stored.")
        return 0

    by_path = {c.source_file: c for c in stored}

    # No arguments: show what's there, so the next command can be specific.
    if not args.documents and not args.all and not args.failed:
        print(f"{len(stored)} document(s) stored:\n")
        for contract in stored:
            marker = "ok    " if contract.status == "extracted" else contract.status
            print(f"  [{marker}] {os.path.basename(contract.source_file)}")
        print("\nPass a name (or fragment) to forget one, or --all to forget everything.")
        return 0

    if args.all:
        if not args.yes:
            answer = input(f"Forget all {len(stored)} document(s)? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Cancelled.")
                return 1
        removed = repository.delete_all()
        print(f"Forgot {removed} document(s). They will be re-extracted on the next run.")
        database.close()
        return 0

    targets = []
    if args.failed:
        targets = [c.source_file for c in stored if c.status != "extracted"]
        if not targets:
            print("No failed documents to forget.")
            database.close()
            return 0

    for term in args.documents:
        matches = _match(list(by_path), term)
        if not matches:
            print(f"No stored document matches {term!r}.")
            database.close()
            return 1
        targets.extend(matches)

    # De-duplicate while keeping order, in case terms overlap.
    targets = list(dict.fromkeys(targets))

    for path in targets:
        if repository.delete(path):
            print(f"Forgot {os.path.basename(path)}")

    print(f"\n{len(targets)} document(s) will be re-extracted on the next run.")
    database.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
