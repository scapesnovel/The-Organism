#!/usr/bin/env python3
"""Founder tool: recover the organism's private key from its backup.

``secrets/private_key_backup.asc`` holds the organism's private key
encrypted TO THE FOUNDER (written at birth, or self-healed on a later
wake). This tool unwraps and decrypts it with YOUR founder private key,
giving you back the exact ORGANISM_PRIVATE_KEY value.

Usage (from the repository root, on your own machine):

    python tools/recover_private_key.py --founder-key founder_PRIVATE_key_v2.asc

    # write the recovered key straight to a file (recommended)
    python tools/recover_private_key.py --founder-key founder_PRIVATE_key_v2.asc ^
        --out organism_key.asc

Requires: pip install pgpy   (pure Python, works on Windows)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core import config  # noqa: E402
from core.encryption import EncryptionManager  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--founder-key", required=True,
        help="file holding YOUR founder PRIVATE key (e.g. founder_PRIVATE_key_v2.asc)",
    )
    parser.add_argument(
        "--backup",
        default=str(REPO_ROOT / config.PRIVATE_KEY_BACKUP_FILE),
        help="path to the backup blob (default: secrets/private_key_backup.asc)",
    )
    parser.add_argument(
        "--out", help="write the recovered key to this file instead of stdout"
    )
    args = parser.parse_args()

    backup_path = Path(args.backup)
    if not backup_path.is_file():
        print(
            f"Backup not found: {backup_path}\n"
            "It is written at birth, or self-healed on the first wake where "
            "both keys load. Pull the latest main and try again.",
            file=sys.stderr,
        )
        return 2

    founder_key_text = Path(args.founder_key).read_text(encoding="utf-8")

    # Decrypt with pgpy directly: the blob is wrapped armor encrypted to
    # the founder's key, so we cannot use the organism's manager (it
    # decrypts with the ORGANISM key, which is what we are recovering).
    try:
        import pgpy
    except ImportError:
        print("pgpy is required: pip install pgpy", file=sys.stderr)
        return 2

    wrapped = backup_path.read_text(encoding="utf-8")
    armor = EncryptionManager.unwrap_payload(wrapped)

    key, _ = pgpy.PGPKey.from_blob(founder_key_text)
    if key.is_public:
        print(
            "That is your PUBLIC key. Recovery needs your PRIVATE key file "
            "(the one you saved outside GitHub).",
            file=sys.stderr,
        )
        return 2
    message = pgpy.PGPMessage.from_blob(armor)
    with key.unlock("") if key.is_protected else _noop(key) as unlocked:
        recovered = unlocked.decrypt(message).message
    if isinstance(recovered, (bytes, bytearray)):
        recovered = recovered.decode("utf-8", errors="replace")

    if args.out:
        Path(args.out).write_text(str(recovered), encoding="utf-8")
        print(f"Recovered key written to {args.out}")
        print("Use it with: python tools/read_memories.py --key-file " + args.out)
    else:
        print(recovered)
    return 0


class _noop:
    """Context manager that yields an already-unlocked key unchanged."""

    def __init__(self, key):
        self.key = key

    def __enter__(self):
        return self.key

    def __exit__(self, *exc):
        return False


if __name__ == "__main__":
    raise SystemExit(main())
