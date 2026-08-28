# Protected area

This directory holds the authoritative manifest of everything the
organism may not modify without explicit approval from the founder
(WISDOM SIFA).

- `manifest.json` — the machine-readable list of protected files and
  directories. It mirrors the enforcement list in `core/loyalty.py`.

The enforcement itself lives in the protected core:

- `core/loyalty.py` — founder authority and write guards.
- `core/kill_switch.py` — the absolute kill switch.
- `core/encryption.py` — PGP primitives (private key only in secrets).
- `core/identity.py` — birth ritual and identity handling.

Changes to this directory require the founder's direct edit or the
approval flow (issue proposal → founder comments `APPROVED`).
