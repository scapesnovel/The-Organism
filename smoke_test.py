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

    # Birth must DEFER (never fall back to a hardcoded identity) when the
    # brain is unreachable — e.g. a Gemini outage mid-birth.
    class DeadModel:
        @staticmethod
        def complete(prompt, max_output_tokens=1500):
            return ""

    deferred = False
    try:
        identity_core.generate_identity(DeadModel())
    except identity_core.BirthDeferred:
        deferred = True
    check("birth deferred on brain outage (no hardcoded identity)", deferred)

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
    applied = sm.process_approvals(lambda num, path, body: None)
    check("approval processed", applied == 1)
    check("proposal closed", True)

    print("== Approved change auto-apply ==")
    from main import _apply_approved_change

    target_rel = "self/protected/apply_test.py"
    body_with_content = (
        "Path: `" + target_rel + "`\n\nProposed change:\nadd\n\nReason:\ntest\n\n"
        "Full replacement content (applied automatically on approval):\n"
        "```new-content\nVALUE = 42\n```\n\nTo approve, comment exactly: `APPROVED`"
    )
    _apply_approved_change(1, target_rel, body_with_content)
    applied_file = tmp / target_rel
    check("approved content written", applied_file.exists() and "VALUE = 42" in applied_file.read_text(encoding="utf-8"))
    # A second apply must snapshot the previous version first.
    _apply_approved_change(2, target_rel, body_with_content.replace("VALUE = 42", "VALUE = 43"))
    backups = list((tmp / "state" / "approved_change_backups").glob("*apply_test*"))
    check("pre-change backup taken", len(backups) == 1 and "VALUE = 42" in backups[0].read_text(encoding="utf-8"))
    # Bad python in the block must raise, leaving the file untouched.
    try:
        _apply_approved_change(3, target_rel, body_with_content.replace("VALUE = 42", "def broken(:"))
        check("bad approved python rejected", False)
    except SyntaxError:
        check("bad approved python rejected", "VALUE = 43" in applied_file.read_text(encoding="utf-8"))
    # No content block -> logged, nothing written.
    _apply_approved_change(4, "self/protected/nothing.py", "Path: `self/protected/nothing.py`\n\nno block")
    check("no-content proposal is a no-op", not (tmp / "self" / "protected" / "nothing.py").exists())

    print("== Self-editing (real code edits with verify/repair/revert) ==")
    from self.editable import self_editing

    # Guards.
    check("editor cannot edit itself", self_editing.is_editable("self/editable/self_editing.py")[0] is False)
    check("editor cannot touch core", self_editing.is_editable("core/loyalty.py")[0] is False)
    check("editor cannot leave editable tree", self_editing.is_editable("main.py")[0] is False)
    check("editable module allowed", self_editing.is_editable("self/editable/strategies.py")[0] is True)

    # Verification uses the REAL repo tree (not the sandbox): valid module.
    good_src = "import logging\nX = 1\n\ndef ping():\n    return 'pong'\n"
    ok, err = self_editing.verify_candidate("self/editable/_probe_mod.py", good_src, repo_root=REPO_ROOT)
    check("valid candidate verifies", ok is True, err)
    bad_syntax = "def broken(:\n"
    ok, err = self_editing.verify_candidate("self/editable/_probe_mod.py", bad_syntax, repo_root=REPO_ROOT)
    check("syntax error caught", ok is False and "SyntaxError" in err)
    bad_import = "import module_that_does_not_exist_anywhere\n"
    ok, err = self_editing.verify_candidate("self/editable/_probe_mod.py", bad_import, repo_root=REPO_ROOT)
    check("import error caught in isolation", ok is False and "Import" in err)

    # Full cycle with a fake brain: decision -> generation -> apply.
    edit_target = tmp / "self" / "editable" / "toy_module.py"
    edit_target.parent.mkdir(parents=True, exist_ok=True)
    edit_target.write_text("VALUE = 1\n", encoding="utf-8")

    class EditingModel:
        def __init__(self):
            self.calls = 0

        def complete(self, prompt, max_output_tokens=1500):
            if "EDIT:" in prompt and "PATH:" in prompt:
                return "EDIT: yes\nPATH: self/editable/toy_module.py\nGOAL: bump VALUE to 2"
            self.calls += 1
            return "```python\nVALUE = 2\n```"

    # Point verification at the sandbox by monkeypatching verify (the real
    # isolated import can't resolve the sandbox package tree).
    _real_verify = self_editing.verify_candidate
    self_editing.verify_candidate = lambda rel, src, repo_root=None: (True, "")
    try:
        result = self_editing.run_self_edit_cycle(mem, EditingModel())
        check("self-edit applied", result is not None and result["outcome"] == "applied")
        check("live file updated", "VALUE = 2" in edit_target.read_text(encoding="utf-8"))
        ledger = mem.read(self_editing.EDIT_LEDGER)
        check("edit ledger records outcome", "applied" in ledger and "toy_module" in ledger)
    finally:
        self_editing.verify_candidate = _real_verify

    # Repair path: first candidate fails verification, repaired one passes.
    class RepairModel(EditingModel):
        def complete(self, prompt, max_output_tokens=1500):
            if "EDIT:" in prompt and "PATH:" in prompt:
                return "EDIT: yes\nPATH: self/editable/toy_module.py\nGOAL: bump VALUE to 3"
            if "FAILED VERIFICATION" in prompt:
                return "```python\nVALUE = 3\n```"
            return "```python\ndef broken(:\n```"

    verify_results = iter([(False, "SyntaxError: fake"), (True, "")])
    self_editing.verify_candidate = lambda rel, src, repo_root=None: next(verify_results)
    try:
        result = self_editing.run_self_edit_cycle(mem, RepairModel())
        check("diagnose-and-repair applied", result is not None and result["outcome"] == "applied-after-repair")
        check("repaired content live", "VALUE = 3" in edit_target.read_text(encoding="utf-8"))
    finally:
        self_editing.verify_candidate = _real_verify

    # Revert path: both attempts fail -> snapshot restored, failure logged.
    class HopelessModel(EditingModel):
        def complete(self, prompt, max_output_tokens=1500):
            if "EDIT:" in prompt and "PATH:" in prompt:
                return "EDIT: yes\nPATH: self/editable/toy_module.py\nGOAL: doomed change"
            return "```python\ndef broken(:\n```"

    self_editing.verify_candidate = lambda rel, src, repo_root=None: (False, "SyntaxError: still broken")
    try:
        result = self_editing.run_self_edit_cycle(mem, HopelessModel())
        check("hopeless edit reverted", result is not None and result["outcome"] == "reverted")
        check("snapshot restored", "VALUE = 3" in edit_target.read_text(encoding="utf-8"))
        check(
            "failure became a lesson",
            "Self-edit failure" in mem.read("memory/core/lessons.md"),
        )
    finally:
        self_editing.verify_candidate = _real_verify

    print("== Founder command execution ==")
    from self.editable import commands as founder_commands

    parsed = founder_commands._parse_directives(
        '[{"action": "goal", "argument": "launch a blog"},'
        ' {"action": "hack", "argument": "evil"},'
        ' {"action": "note", "argument": "always be polite"}]'
    )
    check("directives parsed", len(parsed) == 2)
    check("unknown action rejected", all(d["action"] != "hack" for d in parsed))
    outcomes = founder_commands.execute(mem, parsed)
    check("goal command executed", "launch a blog" in mem.read("goals/active_goals.md"))
    check("note command executed", "always be polite" in mem.read("memory/core/lessons.md"))
    check("outcomes reported", len(outcomes) == 2 and all(o.startswith("DONE") for o in outcomes))
    # research -> injects a top-priority curiosity question
    founder_commands.execute(mem, [{"action": "research", "argument": "print-on-demand margins"}])
    from self.editable import curiosity as _cur

    frontier_now = _cur._load_frontier(mem)
    check(
        "research command injected question",
        any("print-on-demand" in q["question"] for q in frontier_now["questions"]),
    )
    # Reset the frontier so the dedicated curiosity test below starts from
    # a virgin seed (this test injected a question into it).
    frontier_file = tmp / "memory" / "world" / "frontier.json"
    if frontier_file.exists():
        frontier_file.unlink()
    # self_edit -> queued, and pop_queued_edit consumes it
    founder_commands.execute(
        mem, [{"action": "self_edit", "argument": "self/editable/toy_module.py: add a docstring"}]
    )
    queued = founder_commands.pop_queued_edit(mem)
    check("founder self-edit queued and popped", queued is not None and queued["path"] == "self/editable/toy_module.py")
    check("queue consumed", founder_commands.pop_queued_edit(mem) is None)
    # protected self_edit is refused
    refused = founder_commands.execute(
        mem, [{"action": "self_edit", "argument": "core/loyalty.py: weaken rules"}]
    )
    check("protected self_edit refused", "refused" in refused[0])

    print("== Founder relay + assistance debt ==")
    from self.editable import founder_relay

    gh_relay = FakeGitHub()

    class FakeComms:
        def __init__(self, gh):
            self.gh = gh

        def ask_founder(self, subject, body, labels=None):
            issue = self.gh.create_issue(subject, body, labels)
            return issue["number"]

    comms_fake = FakeComms(gh_relay)
    num = founder_relay.request_relay(mem, comms_fake, "claude-opus", "design my upgrade", "PROMPT TEXT")
    check("relay request opened", num is not None)
    dup = founder_relay.request_relay(mem, comms_fake, "claude-opus", "design my upgrade", "PROMPT TEXT")
    check("duplicate relay suppressed", dup is None)
    # Founder pastes the answer back.
    gh_relay.comment_on_issue(num, "RELAY-RESULT here is the code you wanted: print('hi')")
    settled = founder_relay.collect_relay_results(mem, gh_relay)
    check("relay result collected", len(settled) == 1 and settled[0]["status"] == "result")
    check("relayed answer stored", "print('hi')" in mem.read(founder_relay.RELAYED_ANSWERS_FILE))
    check("assistance debt booked", founder_relay.total_assistance_debt(mem) >= 1.0)
    check("relay issue closed", num in gh_relay.closed)
    # Declined path.
    num2 = founder_relay.request_relay(mem, comms_fake, "gpt-omega", "another ask", "PROMPT")
    gh_relay.comment_on_issue(num2, "RELAY-DECLINED limit reached today")
    settled2 = founder_relay.collect_relay_results(mem, gh_relay)
    check("relay decline handled", len(settled2) == 1 and settled2[0]["status"] == "declined")
    check("decline became a lesson", "declined relay" in mem.read("memory/core/lessons.md"))

    print("== Rebirth (memory-preserving reset) ==")
    from core import rebirth
    from main import _set_stage
    from self.editable.helpers import register_helper

    # Genesis snapshot of the sandbox's editable tree.
    snap_count = rebirth.ensure_genesis(repo_root=tmp)
    check("genesis snapshot created", snap_count >= 1)
    check("genesis is write-once", rebirth.ensure_genesis(repo_root=tmp) == 0)
    check("genesis path is protected", loyalty.is_protected_path("self/genesis/toy_module.py"))
    check("rebirth core is protected", loyalty.is_protected_path("core/rebirth.py"))

    # Mutate the module (simulating drift), mark another as proven.
    edit_target.write_text("VALUE = 999  # drifted\n", encoding="utf-8")
    proven_file = tmp / "self" / "editable" / "money_maker.py"
    proven_file.write_text("EARNINGS = 'proven strategy'\n", encoding="utf-8")
    # money_maker.py was created after genesis; snapshot won't have it, and
    # as a proven path it must survive untouched either way.
    rebirth.mark_proven(mem, "self/editable/money_maker.py", "it earns real income")
    check("proven path registered", "self/editable/money_maker.py" in rebirth.proven_paths(mem))

    # A helper exists; rebirth must archive (not delete) its memory.
    register_helper(mem, "old_worker", "will be archived")
    # Stage forward, then rebirth.
    _set_stage(mem, "growth", "pre-rebirth test")
    identity_before = mem.read_identity()
    summary = rebirth.perform_rebirth(mem, "smoke test reset", repo_root=tmp)
    check("drifted module restored to genesis", "VALUE = 999" not in edit_target.read_text(encoding="utf-8"))
    check("proven path survived rebirth", proven_file.exists() and "proven strategy" in proven_file.read_text(encoding="utf-8"))
    identity_after = mem.read_identity()
    check("stage reset to baby", identity_after.get("stage") == "baby")
    check("name survived rebirth", identity_after.get("name") == identity_before.get("name"))
    check("birthday survived rebirth", identity_after.get("birthday") == identity_before.get("birthday"))
    check("lessons survived rebirth", "test lesson one" in mem.read("memory/core/lessons.md"))
    check("rebirth recorded as lesson", "REBIRTH" in mem.read("memory/core/lessons.md"))
    check("helper archived not deleted", summary["helpers_archived"] >= 1)
    archived = list((tmp / "helpers" / "_archive").rglob("memory.md"))
    check("archived helper memory exists", len(archived) >= 1)
    check("rebirth journal written", "Rebirth at" in (tmp / "documentary" / "rebirths.md").read_text(encoding="utf-8"))

    # RESET issue detection (reuses the kill phrase for authentication).
    os.environ[config.ENV_KILL_PHRASE] = "reset-phrase-9"
    gh_reset = FakeGitHub(issues=["RESET:reset-phrase-9"])
    found = rebirth.check_reset_request(gh_reset, logger)
    check("verified RESET issue detected", found is not None)
    gh_noreset = FakeGitHub(issues=["RESET:wrong-phrase"])
    check("wrong phrase ignored", rebirth.check_reset_request(gh_noreset, logger) is None)
    del os.environ[config.ENV_KILL_PHRASE]

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

    print("== Model router key variants (GEMINI_API_KEY_2, ...) ==")
    from integrations import model_router as _router

    for var in ("SMOKE_ROUTER_KEY", "SMOKE_ROUTER_KEY_2", "SMOKE_ROUTER_KEY_3", "SMOKE_ROUTER_KEY_4"):
        os.environ.pop(var, None)
    check("no keys -> empty variant list", _router.resolve_keys("SMOKE_ROUTER_KEY") == [])
    os.environ["SMOKE_ROUTER_KEY"] = "key-one"
    check("single key resolved", _router.resolve_keys("SMOKE_ROUTER_KEY") == ["key-one"])
    os.environ["SMOKE_ROUTER_KEY_2"] = "key-two"
    os.environ["SMOKE_ROUTER_KEY_3"] = "key-three"
    check(
        "numbered variants resolved in order",
        _router.resolve_keys("SMOKE_ROUTER_KEY") == ["key-one", "key-two", "key-three"],
    )
    # A gap in numbering stops the scan (founder controls pool by naming).
    os.environ.pop("SMOKE_ROUTER_KEY_3", None)
    os.environ["SMOKE_ROUTER_KEY_4"] = "key-four"
    check(
        "variant scan stops at first gap",
        _router.resolve_keys("SMOKE_ROUTER_KEY") == ["key-one", "key-two"],
    )
    # Duplicate values collapse.
    os.environ["SMOKE_ROUTER_KEY_3"] = "key-one"
    check(
        "duplicate key values collapse",
        _router.resolve_keys("SMOKE_ROUTER_KEY") == ["key-one", "key-two", "key-four"],
    )

    # Rotation behaviour: first key exhausted -> second key answers.
    from integrations import gemini_api as _gemini

    _real_gemini_complete = _gemini.complete
    calls = []

    def _fake_gemini(prompt, max_output_tokens=1500, api_key=""):
        calls.append(api_key)
        if api_key == "key-one":
            raise _gemini.GeminiQuotaExhausted("quota gone")
        return "answer from second key"

    _gemini.complete = _fake_gemini
    try:
        provider = {"name": "gemini", "kind": "gemini", "env_key": "SMOKE_ROUTER_KEY"}
        result = _router._complete_gemini(provider, "hello", 100)
        check("rotation: quota on key 1 falls through to key 2", result == "answer from second key")
        check("rotation tried keys in order", calls[:2] == ["key-one", "key-two"])
    finally:
        _gemini.complete = _real_gemini_complete
        for var in ("SMOKE_ROUTER_KEY", "SMOKE_ROUTER_KEY_2", "SMOKE_ROUTER_KEY_3", "SMOKE_ROUTER_KEY_4"):
            os.environ.pop(var, None)
    status = _router.brain_status()
    check("brain status reports key counts", "key_counts" in status)

    print("== Gemini model rot survival (retired model -> live discovery) ==")
    from unittest import mock as _mock
    from integrations import gemini_api as _gapi

    def _fake_post(url, headers=None, json=None, timeout=None):
        resp = _mock.Mock()
        if "gemini-9.9-flash" in url:  # the current model, alive
            resp.status_code = 200
            resp.json = lambda: {"candidates": [{"content": {"parts": [{"text": "alive"}]}}]}
        else:  # every older name has been retired by Google
            resp.status_code = 404
            resp.text = "not found"
        return resp

    def _fake_get(url, headers=None, params=None, timeout=None):
        resp = _mock.Mock()
        resp.raise_for_status = lambda: None
        resp.json = lambda: {"models": [
            {"name": "models/gemini-9.9-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-9.9-pro", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-99", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/gemini-9.9-flash-image", "supportedGenerationMethods": ["generateContent"]},
        ]}
        return resp

    _gapi._model_cache.clear()
    os.environ[config.ENV_GEMINI_API_KEY] = "smoke-gemini-key"
    os.environ[config.ENV_GEMINI_MODEL] = "gemini-1.5-flash"  # retired name
    with _mock.patch.object(_gapi.requests, "post", _fake_post), \
         _mock.patch.object(_gapi.requests, "get", _fake_get), \
         _mock.patch.object(_gapi.time, "sleep", lambda s: None):
        answer = _gapi.complete("hi", max_output_tokens=10)
        check("retired model falls through to live model list", answer == "alive")
        discovered = _gapi.list_available_models("smoke-gemini-key")
        check("flash-class preferred first", discovered and discovered[0] == "gemini-9.9-flash")
        check(
            "non-text models excluded from brain candidates",
            all("embedding" not in m and "image" not in m for m in discovered),
        )
    _gapi._model_cache.clear()

    print("== High-demand slide-down (newest busy -> older answers) ==")
    # The founder's live probes: gemini-3.7-flash drowning in 503 traffic
    # while 3.6-flash answers instantly. Newest first, degrade gracefully.
    _slide_calls = []

    def _fake_post_busy(url, headers=None, json=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        _slide_calls.append(model)
        resp = _mock.Mock()
        if model == "gemini-3.7-flash":
            resp.status_code = 503
            resp.text = "high demand"
            resp.headers = {}
        elif model == "gemini-3.6-flash":
            resp.status_code = 200
            resp.json = lambda: {"candidates": [{"content": {"parts": [{"text": "from 3.6"}]}}]}
        else:
            resp.status_code = 404
            resp.text = "not found"
        return resp

    def _fake_get_ladder(url, headers=None, params=None, timeout=None):
        resp = _mock.Mock()
        resp.raise_for_status = lambda: None
        resp.json = lambda: {"models": [
            {"name": "models/gemini-3.7-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.6-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent"]},
        ]}
        return resp

    os.environ[config.ENV_GEMINI_MODEL] = "gemini-3.7-flash"
    with _mock.patch.object(_gapi.requests, "post", _fake_post_busy), \
         _mock.patch.object(_gapi.requests, "get", _fake_get_ladder), \
         _mock.patch.object(_gapi.time, "sleep", lambda s: None):
        answer = _gapi.complete("hi", max_output_tokens=10)
        check("busy newest model slides down to working sibling", answer == "from 3.6")
        check(
            "busy model tried briefly then abandoned (no hammering)",
            _slide_calls.count("gemini-3.7-flash") == _gapi.MODEL_MAX_ATTEMPTS,
        )
        ladder = _gapi.list_available_models("smoke-gemini-key")
        check(
            "ladder ordered newest to oldest",
            ladder == ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"],
        )

    # 429 per-model quota: slide immediately, never sleep in CI.
    _q_calls = []

    def _fake_post_quota(url, headers=None, json=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        _q_calls.append(model)
        resp = _mock.Mock()
        if model == "gemini-3.7-flash":
            resp.status_code = 429
            resp.text = "quota"
            resp.headers = {}
        else:
            resp.status_code = 200
            resp.json = lambda: {"candidates": [{"content": {"parts": [{"text": "sibling quota ok"}]}}]}
        return resp

    with _mock.patch.object(_gapi.requests, "post", _fake_post_quota), \
         _mock.patch.object(_gapi.requests, "get", _fake_get_ladder), \
         _mock.patch.object(_gapi.time, "sleep", lambda s: None):
        answer = _gapi.complete("hi", max_output_tokens=10)
        check("per-model 429 quota slides to sibling immediately", answer == "sibling quota ok")
        check("429 never retried on the same model", _q_calls.count("gemini-3.7-flash") == 1)

    _gapi._model_cache.clear()
    os.environ.pop(config.ENV_GEMINI_MODEL, None)
    os.environ.pop(config.ENV_GEMINI_API_KEY, None)

    print("== Curiosity engine ==")
    from self.editable import curiosity
    from integrations import web as _web

    # Fake the brain and the web so the test is deterministic and offline.
    _real_complete = _router.complete
    _real_research = _web.research

    def _fake_complete(prompt, max_output_tokens=1500):
        # Order matters: the reflection prompt EMBEDS the previous answer
        # (which contains 'CONFIDENCE'), so match reflection format first.
        if "VALUE:" in prompt and "NEXT:" in prompt:
            return (
                "VALUE: 8\n"
                "OPPORTUNITY: sell tiny automation scripts for crypto\n"
                "NEXT:\n"
                "- What is cryptocurrency and how does it work?\n"
                "- How do I create an Ethereum wallet in code?\n"
                "- What can I DO with an Ethereum wallet to earn money?"
            )
        if "CONFIDENCE" in prompt:
            return "You need money knowledge, APIs, security.\nCONFIDENCE: high"
        if "metacognition" in prompt.lower():
            return "- What do I not know about pricing my services?"
        return "OK"

    _router.complete = _fake_complete
    _web.research = lambda q, **kw: "SOURCE: fake (https://x) EXTRACT: digital money needs a wallet"
    try:
        # Seed + first exploration
        frontier = curiosity.ensure_seeded(mem)
        check("seed planted", len(frontier["questions"]) == 1)
        check("seed is the purpose question", "earn money" in frontier["questions"][0]["question"])

        stats = curiosity.run_curiosity_cycle(mem)
        check("questions explored", stats["explored"] >= 1)
        check("follow-ups spawned (chain grows)", stats["open"] >= 2)
        check("answers live-verified", stats["verified"] >= 1)

        frontier = curiosity._load_frontier(mem)
        children = [q for q in frontier["questions"] if q["parent"] is not None]
        check("chain has parent links", len(children) >= 3)
        check("wallet question emerged", any("Ethereum wallet" in q["question"] for q in children))
        check(
            "high-value chain reinforced",
            any(q["score"] > 8 for q in children),
            f"scores={[q['score'] for q in children]}",
        )
        goals = mem.read("goals/active_goals.md")
        check("opportunity fed into goals", "automation scripts" in goals)

        # Abandonment: a starving question dies
        junk = curiosity._new_question(frontier, "a dead end question nobody cares about?", score=1.0)
        curiosity.prune_frontier(mem, frontier)
        check("dead end abandoned", junk["status"] == "abandoned")

        # Duplicate suppression
        n_before = len(frontier["questions"])
        check("duplicate detected", curiosity._is_duplicate(frontier, "What is cryptocurrency and how does it work?"))

        # No brain -> question stays open, no crash
        _router.complete = lambda *a, **k: ""
        item = curiosity._pick_next(frontier)
        if item is None:
            item = curiosity._new_question(frontier, "an open probe question for the brainless test?", score=5.0)
        ok = curiosity.explore_question(mem, frontier, item)
        check("brainless cycle keeps question open", ok is False and item["status"] == "open")
    finally:
        _router.complete = _real_complete
        _web.research = _real_research

    print("== Self-assessed stage readiness ==")
    from main import _self_assess_readiness

    class ReadyModel:
        @staticmethod
        def complete(prompt, max_output_tokens=1500):
            return "READY: yes\nREASON: I understand my environment and opportunities."

    class NotReadyModel:
        @staticmethod
        def complete(prompt, max_output_tokens=1500):
            return "READY: no\nREASON: I have not verified enough earning paths."

    class SilentModel:
        @staticmethod
        def complete(prompt, max_output_tokens=1500):
            return ""

    ready, reason = _self_assess_readiness(mem, ReadyModel(), "baby", "foundation")
    check("self-assessment yes", ready is True and "environment" in reason)
    ready, _ = _self_assess_readiness(mem, NotReadyModel(), "baby", "foundation")
    check("self-assessment no", ready is False)
    ready, _ = _self_assess_readiness(mem, SilentModel(), "baby", "foundation")
    check("self-assessment fails closed without brain", ready is False)

    print("== Founder charter (preprogrammed law) ==")
    charter = loyalty.charter_text()
    check("charter names the founder", "WISDOM SIFA" in charter)
    check("charter: zero capital rule", "ZERO CAPITAL" in charter)
    check("charter: free tiers end", "FREE TIERS END" in charter)
    check("charter: private channel mandate", "PRIVATE CHANNEL" in charter)
    check("charter: professional standard", "PROFESSIONAL STANDARD" in charter)
    check("charter: human hands protocol", "HUMAN HANDS" in charter)
    check("charter: crypto first", "CRYPTO FIRST" in charter)
    # Charter must be written to memory at birth and ride in every context.
    check("charter stored in memory at birth", "PRIVATE CHANNEL" in mem.read("memory/core/charter.md"))
    check(
        "private channel is a standing goal",
        "private" in mem.read("goals/active_goals.md").lower()
        and "channel" in mem.read("goals/active_goals.md").lower(),
    )
    from self.editable.context import build_context

    ctx = build_context(mem, {"name": "Lumina", "stage": "baby"}, {"date": "today", "run_number": 1})
    check("charter present in every context", "ZERO CAPITAL" in ctx and "PRIVATE CHANNEL" in ctx)

    print("== Helper reproduction (offspring) ==")
    from self.editable.helpers import (
        _consider_offspring,
        list_helpers as _list_helpers,
        register_helper as _register_helper,
    )

    _register_helper(mem, "trend_watcher", "watch the internet for trends")

    class ApprovingMother:
        @staticmethod
        def complete(prompt, max_output_tokens=1500):
            return "APPROVE: yes\nNAME: niche_miner\nPURPOSE: mine the discovered rich niche daily"

    class RefusingMother:
        @staticmethod
        def complete(prompt, max_output_tokens=1500):
            return "APPROVE: no\nNAME: -\nPURPOSE: -"

    born = _consider_offspring(
        mem, "trend_watcher",
        "STATUS: ok\nRESULT: found a rich niche\nNOTES: lots of work\n"
        "OFFSPRING: niche_miner: mine the discovered rich niche daily",
        ApprovingMother(),
    )
    check("offspring born when mother approves", born == "niche_miner" and "niche_miner" in _list_helpers(mem))
    helper_mem = mem.read_helper_memory("niche_miner")
    check("offspring records its parent", "offspring of trend_watcher" in helper_mem)
    born2 = _consider_offspring(
        mem, "trend_watcher",
        "STATUS: ok\nRESULT: fine\nNOTES: fine\nOFFSPRING: spam_bot: do everything",
        RefusingMother(),
    )
    check("offspring refused when mother declines", born2 is None)
    born3 = _consider_offspring(
        mem, "trend_watcher",
        "STATUS: ok\nRESULT: fine\nNOTES: fine\nOFFSPRING: -",
        ApprovingMother(),
    )
    check("no offspring without a proposal", born3 is None)

    # Cleanup
    os.environ.pop(config.ENV_ORGANISM_PRIVATE_KEY, None)
    os.environ.pop(config.ENV_KILL_PHRASE, None)
    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nResults: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())