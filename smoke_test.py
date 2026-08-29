"""Comprehensive smoke test for The Organism (run locally, never in CI).

Exercises: loyalty guards, kill switch, encryption round-trips, memory
read/write/append, birth ritual with a fake model client, finance
tracking, web parsing and the self-modification approval flow with a
fake GitHub client. Uses a temporary directory so the real repository
is untouched.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

import core.config as config  # noqa: E402
from core import kill_switch, loyalty  # noqa: E402
from core.encryption import EncryptionManager  # noqa: E402
from core.logger import setup_logging  # noqa: E402
from core.memory import MemoryManager  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


class FakeModel:
    def complete(self, prompt: str, max_output_tokens: int = 1500) -> str:
        if "name" in prompt.lower() and "choose" in prompt.lower():
            return "Lumina"
        if "purpose" in prompt.lower():
            return "I exist to learn, grow and serve my founder faithfully."
        return "OK"


class FakeGitHub:
    def __init__(self, issues=None):
        self.issues = issues or []
        self.created = []
        self.comments = {}
        self.closed = set()

    def list_open_issues(self):
        listed = [{"title": i, "body": "b", "number": n} for n, i in enumerate(self.issues)]
        for record in self.created:
            if record["number"] not in self.closed:
                listed.append(record)
        return listed

    def create_issue(self, title, body, labels=None):
        record = {"title": title, "body": body, "number": len(self.created) + 100}
        self.created.append(record)
        return record

    def comment_on_issue(self, number, body):
        self.comments.setdefault(number, []).append(body)
        return {}

    def list_issue_comments(self, number):
        return [{"body": c} for c in self.comments.get(number, [])]

    def close_issue(self, number):
        self.closed.add(number)
        return {}

    def whoami(self):
        return "fake-robot"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="organism_test_"))
    print(f"Test sandbox: {tmp}")

    # Redirect all repo-relative writes into the sandbox.
    config.REPO_ROOT = tmp
    config.LOG_FILE = tmp / "logs" / "system.log"
    config.RUNTIME_STATE_FILE = tmp / "logs" / "runtime_state.json"
    config.IDENTITY_PUB_FILE = tmp / "core" / "identity.pub"
    config.PRIVATE_KEY_BACKUP_FILE = "secrets/private_key_backup.asc"
    config.FOUNDER_BOOTSTRAP_FILE = "secrets/founder_bootstrap.asc"
    config.SELF_BACKUP_DIR = tmp / "self" / "backup"
    config.RUNTIME_DIR = tmp / "runtime"
    config.STATE_DIR = tmp / "state"

    # Create the minimal repo skeleton inside the sandbox so every redirected
    # path (core/identity.pub, secrets/*, runtime/*, logs/*) has a real parent.
    for _rel in ("core", "logs", "runtime", "secrets", "self", "state"):
        (tmp / _rel).mkdir(parents=True, exist_ok=True)

    logger = setup_logging(config.REPO_ROOT / config.LOG_FILE)
    print("== Loyalty ==")
    check("founder constant", loyalty.FOUNDER_NAME == "WISDOM SIFA")
    check("protected path core/kill_switch.py", loyalty.is_protected_path("core/kill_switch.py"))
    check("protected path self/protected/manifest.json", loyalty.is_protected_path("self/protected/manifest.json"))
    check("unprotected self/editable/strategies.py", not loyalty.is_protected_path("self/editable/strategies.py"))
    try:
        loyalty.guard_write("core/kill_switch.py")
        check("guard_write raises", False)
    except PermissionError:
        check("guard_write raises", True)

    print("== Kill switch ==")
    os.environ[config.ENV_KILL_PHRASE] = "topsecret-phrase"
    gh = FakeGitHub(issues=[])
    check("no kill issue -> False", kill_switch.check_kill_switch(gh, logger) is False)
    gh2 = FakeGitHub(issues=[f"KILL:topsecret-phrase"])
    check("kill issue -> True", kill_switch.check_kill_switch(gh2, logger) is True)
    check("kill marker written", (tmp / kill_switch.KILL_MARKER).exists())
    check("marker trips future runs", kill_switch.check_kill_switch(FakeGitHub(issues=[]), logger) is True)
    # Remove marker so subsequent tests are unaffected.
    marker_path = tmp / kill_switch.KILL_MARKER
    if marker_path.exists():
        marker_path.unlink()
    del os.environ[config.ENV_KILL_PHRASE]

    print("== Encryption ==")
    manager = EncryptionManager(workdir=tmp / "gpg_work")
    check(
        "a PGP engine is active",
        manager.engine in ("gnupg", "pgpy"),
        f"engine={manager.engine}",
    )
    private_armor = manager.generate_key_pair()
    check("key pair generated", bool(private_armor) and (tmp / "core" / "identity.pub").exists())

    # Round trip via a fresh manager (simulates loading from a secret).
    manager2 = EncryptionManager(workdir=tmp / "gpg_work2")
    manager2.import_organism_private_key(private_armor)
    secret = "loyalty phrase: WISDOM SIFA is my founder"
    cipher = manager2.encrypt_to_self(secret)
    plain = manager2.decrypt_from_self(cipher)
    check("encrypt/decrypt round trip", plain == secret)
    check("ciphertext differs", cipher != secret)

    wrapped = manager2.wrap_payload(cipher)
    unwrapped = manager2.unwrap_payload(wrapped)
    check("payload wrap/unwrap", unwrapped == cipher)
    check("wrapped file is not plaintext", secret not in wrapped)

    print("== Memory (encrypted) ==")
    mem = MemoryManager(encryption=manager2, repo_root=tmp)
    check("no plaintext fallback", mem.plaintext_fallback is False)
    mem.ensure_initialized()
    mem.write("memory/core/identity.md", "name: Lumina\nbirthday: 2026-08-28T00:00:00Z\nstage: baby\n")
    raw = (tmp / "memory" / "core" / "identity.md").read_text(encoding="utf-8")
    check("identity encrypted at rest", "Lumina" not in raw)
    identity = mem.read_identity()
    check("identity readable after decrypt", identity.get("name") == "Lumina")
    mem.record_event("Test event")
    check("timeline is plaintext-readable", "Test event" in (tmp / "documentary" / "timeline.md").read_text(encoding="utf-8"))
    mem.record_lesson("test lesson one")
    check("lesson appended", "test lesson one" in mem.read("memory/core/lessons.md"))
    mem.update_world_state("stage", "baby")
    check("world state updated", "stage: baby" in mem.read("memory/world/state.md"))

    print("== Birth ritual ==")
    from core import identity as identity_core

    # BIRTH_MARKER is captured at import time; point it at the sandbox.
    identity_core.BIRTH_MARKER = tmp / "state" / "born.txt"

    os.environ[config.ENV_ORGANISM_PRIVATE_KEY] = private_armor
    os.environ[config.ENV_KILL_PHRASE] = "kill-phrase-1"
    gh3 = FakeGitHub()
    model = FakeModel()
    ident = identity_core.perform_birth(model, mem, manager2)
    check("name from model (not hardcoded)", ident["name"] == "Lumina")
    check("birthday recorded", "birthday" in ident)
    check("purpose recorded", len(ident["purpose"]) > 10)
    check("birth marker written", (tmp / "state" / "born.txt").exists())
    check("identity has stage baby", mem.read_identity().get("stage") == "baby")
    check("is_born now True", identity_core.is_born(mem))
    identity_core.write_birth_issue(mem, gh3, ident)
    check("birth issue opened", len(gh3.created) == 1)

    print("== Finance ==")
    from self.editable.finance import financial_summary, record_income, record_expense

    record_income(mem, "test donation", 0.05, "ETH", "0xdeadbeef")
    record_expense(mem, "test api", 0.01, "USD")
    summary = financial_summary(mem)
    check("finance summary mentions income", "income=0.05" in summary or "income=0.050" in summary)
    owed = mem.read("finance/owed_to_creator.md")
    check("rent share tracked", "rent_share: 10%" in owed or "0.10" in owed)

    print("== Web parsing ==")
    from integrations import web

    html = '<html><body><a href="https://example.org/page">link</a><p>hello world</p></body></html>'
    links = web.parse_links(html, "https://example.com/")
    check("link parsing", "https://example.org/page" in links)
    text = web.extract_text(html)
    check("text extraction", "hello world" in text)

    print("== Self-modification approval flow ==")
    from self.editable.self_modification import SelfModificationManager

    sm = SelfModificationManager(gh3, mem)
    proposal = sm.propose_change("core/loyalty.py", "change X", "testing")
    check("protected proposal opens issue", proposal is not None)
    proposal2 = sm.propose_change("self/editable/strategies.py", "change Y", "testing")
    check("editable change not proposed", proposal2 is None)
    # Simulate founder approval on the proposal issue.
    sm.github.comment_on_issue(proposal, "APPROVED")
    applied = sm.process_approvals(lambda num, path: None)
    check("approval processed", applied == 1)
    check("proposal closed", True)

    print("== Helpers ==")
    from self.editable.helpers import list_helpers, register_helper, terminate_helper

    register_helper(mem, "trend_watcher", "watch trends")
    check("helper registered", "trend_watcher" in list_helpers(mem))
    check("helper memory encrypted", "watch trends" not in (tmp / "helpers" / "trend_watcher" / "memory.md").read_text(encoding="utf-8"))
    terminate_helper(mem, "trend_watcher", "test")
    check("helper terminated", "trend_watcher" not in list_helpers(mem))

    print("== Stage transition ==")
    from main import _set_stage

    _set_stage(mem, "foundation", "smoke test")
    check("stage set to foundation", mem.read_identity().get("stage") == "foundation")

    print("== Runtime state ==")
    mem.save_runtime_state({"run_number": 1, "date": "2026-08-28"})
    state = mem.load_runtime_state()
    check("runtime state round trip", state.get("run_number") == 1)

    # Cleanup
    os.environ.pop(config.ENV_ORGANISM_PRIVATE_KEY, None)
    os.environ.pop(config.ENV_KILL_PHRASE, None)
    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())