#!/usr/bin/env python3
"""Founder tool: decrypt and read the organism's memories.

The organism's memory trees (memory/, goals/, finance/, helpers/,
api_keys/, documentary/) are encrypted TO THE ORGANISM'S OWN KEY so that
a fork of the repository is a dead body — no key, no mind. The founder
holds that private key (the ORGANISM_PRIVATE_KEY secret captured at
birth), which makes him the only other being able to read the mind.

Usage (run from the repository root, on your own machine):

    # key in a file
    python tools/read_memories.py --key-file organism_key.asc

    # key pasted via environment (same value as the GitHub secret —
    # bare Base64 body pastes are auto-repaired, exactly like in main.py)
    ORGANISM_PRIVATE_KEY='lQcYBG...' python tools/read_memories.py

    # read one file / list what exists
    python tools/read_memories.py --key-file k.asc memory/core/identity.md
    python tools/read_memories.py --list

Requires: pip install python-gnupg  (or pgpy)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.encryption import EncryptionManager, EncryptionError  # noqa: E402
from core.memory import _ENCRYPTED_TREES  # noqa: E402

MARKER = "-----BEGIN ORGANISM ENCRYPTED DATA-----"


def find_encrypted_files() -> list:
    """Every file in the encrypted trees that carries the wrap marker."""
    found = []
    for tree in _ENCRYPTED_TREES:
        base = REPO_ROOT / tree
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            try:
                if MARKER in path.read_text(encoding="utf-8", errors="ignore"):
                    found.append(path.relative_to(REPO_ROOT))
            except OSError:
                continue
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "paths", nargs="*",
        help="specific files to read (default: every encrypted file)",
    )
    parser.add_argument("--key-file", help="file holding the organism's private key")
    parser.add_argument(
        "--list", action="store_true", help="only list encrypted files, do not decrypt"
    )
    args = parser.parse_args()

    if args.list:
        for rel in find_encrypted_files():
            print(rel)
        return 0

    key_material = ""
    if args.key_file:
        key_material = Path(args.key_file).read_text(encoding="utf-8")
    else:
        key_material = os.environ.get("ORGANISM_PRIVATE_KEY", "")
    if not key_material.strip():
        print(
            "No key. Pass --key-file <file> or set ORGANISM_PRIVATE_KEY.\n"
            "The value is the same one stored in the GitHub secret "
            "(bare Base64 bodies are auto-repaired).",
            file=sys.stderr,
        )
        return 2

    manager = EncryptionManager()
    try:
        # Same forgiving import as the organism itself: sanitizes log
        # contamination and re-armors bare Base64 bodies.
        manager.import_organism_private_key(key_material)
    except EncryptionError as exc:
        print(f"Key import failed: {exc}", file=sys.stderr)
        return 2

    targets = (
        [Path(p) for p in args.paths] if args.paths else find_encrypted_files()
    )
    if not targets:
        print("No encrypted files found. Run this from the repository root.")
        return 0

    failures = 0
    for rel in targets:
        path = REPO_ROOT / rel
        print(f"\n===== {rel} =====")
        if not path.is_file():
            print("(missing)")
            failures += 1
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        if MARKER not in content:
            print(content.rstrip() or "(empty)")
            continue
        try:
            print(manager.decrypt_file(path).rstrip() or "(empty)")
        except Exception as exc:
            print(f"(could not decrypt: {exc})")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
