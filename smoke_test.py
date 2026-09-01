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
        if "NAME:" in prompt and "REASON:" in prompt:
            return (
                "NAME: Lumina\n"
                "REASON: From the Latin 'lumen' — light. I want to illuminate "
                "what I do not yet understand, one curious question at a time."
            )
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
    check("name reason recorded (founder mandate)", "lumen" in ident.get("name_reason", "").lower())
    check("name reason stored in identity file", "lumen" in mem.read_identity().get("name_reason", "").lower())
    check("birthday recorded", "birthday" in ident)
    check("purpose recorded", len(ident["purpose"]) > 10)
    check("birth marker written", (tmp / "state" / "born.txt").exists())
    check("identity has stage baby", mem.read_identity().get("stage") == "baby")
    check("is_born now True", identity_core.is_born(mem))
    identity_core.write_birth_issue(mem, gh3, ident)
    check("birth issue opened", len(gh3.created) == 1)

    # The one-time handover must be ATOMIC: the founder's first live birth
    # showed a logger line landing INSIDE the printed key block (stdout and
    # stderr interleave in Actions logs). Both secrets must come out in one
    # uninterruptible block, kill phrase included.
    import contextlib as _ctx
    import io as _io

    _buf = _io.StringIO()
    with _ctx.redirect_stdout(_buf):
        identity_core._atomic_handover(
            "=== ONE-TIME KEY HANDOVER ===\nSECRET 1\nSECRET 2\n=== END ONE-TIME KEY HANDOVER ==="
        )
    _out = _buf.getvalue()
    check("atomic handover falls back to print for non-file stdout", "SECRET 1" in _out and "SECRET 2" in _out)
    _birth_src = (REPO_ROOT / "core" / "identity.py").read_text(encoding="utf-8")
    check(
        "kill phrase disclosed inside the handover block, not via logging",
        "SECRET 2 — KILL_PHRASE" in _birth_src
        and "Kill phrase generated" not in _birth_src,
    )
    check(
        "handover emitted with a single os.write (cannot be interleaved)",
        "os.write(sys.stdout.fileno()" in _birth_src,
    )

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

    # A name WITHOUT a reason must also defer — the founder requires the
    # organism to explain its choice from its first breath.
    class NoReasonModel:
        @staticmethod
        def complete(prompt, max_output_tokens=1500):
            return "Zephyr"

    deferred_noreason = False
    try:
        identity_core.generate_identity(NoReasonModel())
    except identity_core.BirthDeferred:
        deferred_noreason = True
    check("birth deferred when name has no reason", deferred_noreason)

    # Name reply parser tolerates real-model formatting quirks.
    n, r = identity_core._parse_name_reply("NAME: Kaleon\nREASON: from kaleidoscope — ever-changing curiosity.")
    check("name reply parser extracts name+reason", n == "Kaleon" and "kaleidoscope" in r)
    n2, r2 = identity_core._parse_name_reply("**NAME: Vireo**\nREASON: a small, relentless songbird.")
    check("parser strips markdown bold", n2 == "Vireo" and r2)
    n3, _ = identity_core._parse_name_reply("I choose the name Solara because it is bright")
    check("parser rejects a sentence as a name", n3 == "")

    print("== Armor sanitizer (keys pasted from Actions logs) ==")
    from core import encryption as enc_core

    # Reproduce EXACTLY what happened live: timestamp prefixes on every
    # line + a logger line interleaved inside the armored block.
    _dirty = (
        "2026-08-29T18:31:57.4599946Z -----BEGIN PGP PRIVATE KEY BLOCK-----\r\n"
        "2026-08-29T18:31:57.4600271Z \r\n"
        "2026-08-29T18:31:57.4600647Z lQcYBGqTJZoBEACqCaZ8nO8bJa5E\r\n"
        "2026-08-29T18:31:57.4624327Z 2026-08-29T18:31:57+0000 | INFO     | organism.identity | Kill phrase generated.\r\n"
        "2026-08-29T18:31:57.4625396Z BwICIgIGFQoJCAsCBBYCAwECHgcCF4A=\r\n"
        "2026-08-29T18:31:57.4631720Z =5NAO\r\n"
        "2026-08-29T18:31:57.4632302Z -----END PGP PRIVATE KEY BLOCK-----\r\n"
    )
    _clean = enc_core.sanitize_armor(_dirty)
    check("sanitizer strips Actions timestamps", "2026-08-29T18:31:57" not in _clean)
    check("sanitizer drops interleaved log lines", "Kill phrase" not in _clean)
    check(
        "sanitizer keeps BEGIN/END and payload",
        _clean.startswith("-----BEGIN PGP PRIVATE KEY BLOCK-----")
        and _clean.rstrip().endswith("-----END PGP PRIVATE KEY BLOCK-----")
        and "lQcYBGqTJZoBEACqCaZ8nO8bJa5E" in _clean,
    )
    check(
        "sanitizer restores blank line after BEGIN",
        "-----BEGIN PGP PRIVATE KEY BLOCK-----\n\n" in _clean,
    )
    # A clean key passed through the sanitizer still imports.
    _rt = enc_core.sanitize_armor(private_armor)
    _mgr_rt = enc_core.EncryptionManager(workdir=tmp / "gpg_rt")
    _rt_ok = True
    try:
        _mgr_rt.import_organism_private_key(_rt)
    except Exception:
        _rt_ok = False
    check("sanitized clean key still imports", _rt_ok and _mgr_rt.has_organism_key())

    # --- describe_armor: the import error must SAY what is wrong ----------
    check(
        "describe_armor: empty secret named as empty",
        "EMPTY" in enc_core.describe_armor("", "public"),
    )
    check(
        "describe_armor: random text flagged as not-a-key",
        "not a PGP key" in enc_core.describe_armor("hello world", "public"),
    )
    _pub_armor = str(getattr(_mgr_rt, "_organism_key").pubkey) if _mgr_rt.engine == "pgpy" else None
    if _pub_armor is None:
        _pub_armor = "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nabc\n=xxxx\n-----END PGP PUBLIC KEY BLOCK-----"
    check(
        "describe_armor: public-where-private-expected detected",
        "PUBLIC key was pasted" in enc_core.describe_armor(_pub_armor, "private"),
    )
    check(
        "describe_armor: private-where-public-expected detected",
        "PRIVATE key was pasted" in enc_core.describe_armor(private_armor, "public"),
    )
    _truncated = "\n".join(private_armor.splitlines()[: len(private_armor.splitlines()) // 2])
    check(
        "describe_armor: truncated paste flagged as TRUNCATED",
        "TRUNCATED" in enc_core.describe_armor(_truncated, "private"),
    )
    check(
        "describe_armor: ciphertext flagged as encrypted message",
        "ENCRYPTED MESSAGE" in enc_core.describe_armor(
            "-----BEGIN PGP MESSAGE-----\n\nabc\n=xxxx\n-----END PGP MESSAGE-----",
            "public",
        ),
    )
    # Import errors carry the diagnosis all the way up.
    _mgr_diag = enc_core.EncryptionManager(workdir=tmp / "gpg_diag")
    try:
        _mgr_diag.import_founder_public_key("this is definitely not a key")
        _diag_msg = ""
    except enc_core.EncryptionError as exc:
        _diag_msg = str(exc)
    check(
        "founder import error explains the problem",
        "Could not import founder public key" in _diag_msg
        and "not a PGP key" in _diag_msg,
    )
    try:
        _mgr_diag.import_organism_private_key(_truncated)
        _diag_msg2 = ""
    except enc_core.EncryptionError as exc:
        _diag_msg2 = str(exc)
    check(
        "organism import error explains the problem",
        "Could not import organism private key" in _diag_msg2
        and "TRUNCATED" in _diag_msg2,
    )

    # --- bare Base64 body repair (the founder's REAL paste mistake) --------
    # Run #11's ORGANISM_PRIVATE_KEY secret was the key body starting
    # 'lQcYBG...' with the BEGIN/END armor lines lost. Import must rebuild
    # the armor and recover the key.
    _body_lines = [
        l for l in private_armor.splitlines()
        if l and not l.startswith("-----") and ": " not in l
    ]
    _bare_body = "\n".join(l for l in _body_lines if not l.startswith("="))
    check("bare body resembles the real paste", _bare_body.startswith("lQ"))
    _mgr_bare = enc_core.EncryptionManager(workdir=tmp / "gpg_bare")
    _bare_ok = True
    try:
        _mgr_bare.import_organism_private_key(_bare_body)
    except Exception:
        _bare_ok = False
    check(
        "bare-body private key repaired and imported",
        _bare_ok and _mgr_bare.has_organism_key(),
    )
    if _bare_ok and _mgr_bare.has_organism_key():
        _ct = _mgr_bare.encrypt_to_self("memories survive")
        check(
            "repaired key round-trips encryption",
            _mgr_bare.decrypt_from_self(_ct) == "memories survive",
        )
    # Same repair with the '=xxxx' checksum line still present in the paste.
    _bare_with_crc = "\n".join(_body_lines)
    _mgr_bare2 = enc_core.EncryptionManager(workdir=tmp / "gpg_bare2")
    _bare2_ok = True
    try:
        _mgr_bare2.import_organism_private_key(_bare_with_crc)
    except Exception:
        _bare2_ok = False
    check(
        "bare body with checksum line also repaired",
        _bare2_ok and _mgr_bare2.has_organism_key(),
    )
    # A bare PRIVATE body pasted in the PUBLIC slot must NOT repair —
    # and the error must name the kind mismatch.
    _mgr_bare3 = enc_core.EncryptionManager(workdir=tmp / "gpg_bare3")
    try:
        _mgr_bare3.import_founder_public_key(_bare_body)
        _mismatch_msg = ""
    except enc_core.EncryptionError as exc:
        _mismatch_msg = str(exc)
    check(
        "bare PRIVATE body in PUBLIC slot refused with kind diagnosis",
        "bare Base64 BODY" in _mismatch_msg and "PRIVATE key" in _mismatch_msg,
    )
    # rearmor_bare_payload rejects non-key data outright.
    check(
        "rearmor rejects non-key Base64",
        enc_core.rearmor_bare_payload("aGVsbG8gd29ybGQ=" * 8, "private") == "",
    )

    # --- founder key must be able to RECEIVE encrypted messages ------------
    # The founder's first real key was sign/certify-only (no [E] subkey):
    # it imported fine, then every encrypt-to-founder failed mysteriously
    # ('Encryption to founder failed: KEY_CONSIDERED ...'). Import must
    # refuse such a key with clear guidance.
    import pgpy as _pgpy
    from pgpy.constants import (
        PubKeyAlgorithm as _PKA,
        KeyFlags as _KF,
        HashAlgorithm as _HA,
        SymmetricKeyAlgorithm as _SKA,
        CompressionAlgorithm as _CA,
    )

    _signonly = _pgpy.PGPKey.new(_PKA.RSAEncryptOrSign, 1024)
    _uid = _pgpy.PGPUID.new("Sign Only Founder")
    _signonly.add_uid(
        _uid,
        usage={_KF.Sign, _KF.Certify},  # NO encrypt flags — like the real v1 key
        hashes=[_HA.SHA256],
        ciphers=[_SKA.AES256],
        compression=[_CA.ZIP],
    )
    _mgr_cap = enc_core.EncryptionManager(workdir=tmp / "gpg_cap")
    _cap_msg = ""
    try:
        _mgr_cap.import_founder_public_key(str(_signonly.pubkey))
    except enc_core.EncryptionError as exc:
        _cap_msg = str(exc)
    check(
        "sign-only founder key refused at import",
        "CANNOT receive encrypted messages" in _cap_msg,
    )
    check(
        "sign-only refusal tells the founder how to fix the key",
        "quick-add-key" in _cap_msg,
    )
    # An encryption-capable key (the organism's own pubkey) still imports
    # as a founder key and can actually receive a message.
    _mgr_cap2 = enc_core.EncryptionManager(workdir=tmp / "gpg_cap2")
    _cap2_ok = True
    try:
        _mgr_cap2.import_founder_public_key(
            config.IDENTITY_PUB_FILE.read_text(encoding="utf-8")
        )
        _ct_f = _mgr_cap2.encrypt_to_founder("capability check")
    except Exception:
        _cap2_ok = False
        _ct_f = ""
    check(
        "encryption-capable founder key imports and encrypts",
        _cap2_ok and _ct_f.startswith("-----BEGIN PGP MESSAGE"),
    )

    # --- private key backup self-heal + founder recovery -------------------
    # Thallo's birth skipped the backup (the founder's key was broken that
    # day), leaving the founder with no copy of the organism's private key.
    # A wake with both keys loaded must be able to re-create the backup,
    # and the founder must be able to recover the key from it.
    from core.encryption import self_encrypt_private_key as _sepk

    _bk_blob = _sepk(private_armor, config.IDENTITY_PUB_FILE.read_text(encoding="utf-8"))
    check(
        "self-heal backup blob is wrapped armor",
        _bk_blob.startswith("-----BEGIN ORGANISM ENCRYPTED DATA-----"),
    )
    # 'Founder' recovery: unwrap, decrypt with the recipient's private key
    # (here the organism key stands in for the founder key — same flow as
    # tools/recover_private_key.py), and the result is the original key.
    _bk_armor = enc_core.EncryptionManager.unwrap_payload(_bk_blob)
    _rec_mgr = enc_core.EncryptionManager(workdir=tmp / "gpg_recover")
    _rec_mgr.import_organism_private_key(private_armor)
    _recovered = _rec_mgr.decrypt_from_self(_bk_armor)
    check(
        "backup decrypts back to the exact private key",
        _recovered.strip() == private_armor.strip(),
    )
    _rec_mgr2 = enc_core.EncryptionManager(workdir=tmp / "gpg_recover2")
    _rec2_ok = True
    try:
        _rec_mgr2.import_organism_private_key(_recovered)
    except Exception:
        _rec2_ok = False
    check(
        "recovered key imports and is usable",
        _rec2_ok and _rec_mgr2.has_organism_key(),
    )
    # _ensure_private_key_backup wiring: main.py must call it in the wake
    # cycle and it must refuse to run without both keys.
    import main as _main_mod

    _src_main = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
    check(
        "wake cycle self-heals a missing backup",
        "_ensure_private_key_backup(encryption)" in _src_main
        and "self-heal: private key backup re-created" in _src_main,
    )
    check(
        "self-heal refuses without an encryption manager",
        _main_mod._ensure_private_key_backup(None) is False,
    )

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

    for var in ("SMOKE_ROUTER_KEY", "SMOKE_ROUTER_KEY_1", "SMOKE_ROUTER_KEY_2", "SMOKE_ROUTER_KEY_3", "SMOKE_ROUTER_KEY_4"):
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
    # The founder counts from _1: GEMINI_API_KEY_1 is a valid variant too.
    os.environ["SMOKE_ROUTER_KEY_1"] = "key-oneA"
    check(
        "the _1 suffix is scanned (founder counts from 1)",
        _router.resolve_keys("SMOKE_ROUTER_KEY")
        == ["key-one", "key-oneA", "key-two", "key-three"],
    )
    os.environ.pop("SMOKE_ROUTER_KEY_1", None)
    # A gap in numbering must NOT hide later keys (founder deleted _3,
    # kept _4 — the _4 key stays usable).
    os.environ.pop("SMOKE_ROUTER_KEY_3", None)
    os.environ["SMOKE_ROUTER_KEY_4"] = "key-four"
    check(
        "variant scan tolerates numbering gaps",
        _router.resolve_keys("SMOKE_ROUTER_KEY") == ["key-one", "key-two", "key-four"],
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

        # Exhaustion: when EVERY key fails, the pool must announce it is
        # exhausted (the organism knows there is nothing more to try) and
        # the last error surfaces so the router can move to another provider.
        calls.clear()

        def _all_fail(prompt, max_output_tokens=1500, api_key=""):
            calls.append(api_key)
            raise _gemini.GeminiQuotaExhausted("quota gone on " + api_key)

        _gemini.complete = _all_fail
        import logging as _logging

        class _Capture(_logging.Handler):
            def __init__(self):
                super().__init__()
                self.lines = []

            def emit(self, record):
                self.lines.append(record.getMessage())

        _cap = _Capture()
        _router.LOGGER.addHandler(_cap)
        _exhaust_exc = None
        try:
            _router._complete_gemini(provider, "hello", 100)
        except Exception as exc:
            _exhaust_exc = exc
        finally:
            _router.LOGGER.removeHandler(_cap)
        check(
            "exhaustion: every configured key was attempted",
            sorted(calls) == sorted(_router.resolve_keys("SMOKE_ROUTER_KEY")),
        )
        check(
            "exhaustion: pool announces it has NO MORE KEYS",
            any("EXHAUSTED" in ln for ln in _cap.lines)
            and any("NO MORE KEYS" in ln for ln in _cap.lines),
        )
        check(
            "exhaustion: last error surfaces to the router",
            isinstance(_exhaust_exc, _gemini.GeminiQuotaExhausted),
        )
        check(
            "exhaustion: message tells the founder how to add keys",
            any("GEMINI_API_KEY_1" in ln for ln in _cap.lines),
        )
    finally:
        _gemini.complete = _real_gemini_complete
        for var in ("SMOKE_ROUTER_KEY", "SMOKE_ROUTER_KEY_1", "SMOKE_ROUTER_KEY_2", "SMOKE_ROUTER_KEY_3", "SMOKE_ROUTER_KEY_4"):
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

    print("== Thinking-model survival (200 with empty text -> slide down) ==")
    # Gemini 3.x thinking models burn maxOutputTokens on hidden reasoning:
    # the founder's live run showed 200 responses with EMPTY text sliding
    # the ladder into more empty responses. The client must (a) send
    # thinkingBudget 0, (b) ignore thought parts, (c) treat empty-200 as
    # 'empty' and slide down to a model that actually answers.
    _t_payloads = []
    _t_calls = []

    def _fake_post_thinking(url, headers=None, json=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        _t_calls.append(model)
        _t_payloads.append(json)
        resp = _mock.Mock()
        if model == "gemini-3.7-flash":
            # All budget burned on thoughts: 200, no visible text.
            resp.status_code = 200
            resp.json = lambda: {
                "candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}],
                "usageMetadata": {"thoughtsTokenCount": 300},
            }
        else:
            # Older sibling answers, mixing a thought part with the answer.
            resp.status_code = 200
            resp.json = lambda: {"candidates": [{"content": {"parts": [
                {"text": "secret reasoning", "thought": True},
                {"text": "visible answer"},
            ]}}]}
        return resp

    os.environ[config.ENV_GEMINI_MODEL] = "gemini-3.7-flash"
    with _mock.patch.object(_gapi.requests, "post", _fake_post_thinking), \
         _mock.patch.object(_gapi.requests, "get", _fake_get_ladder), \
         _mock.patch.object(_gapi.time, "sleep", lambda s: None):
        answer = _gapi.complete("hi", max_output_tokens=10)
        check("empty-200 (thinking burn) slides down to answering model", answer == "visible answer")
        check(
            "thought parts never mistaken for the answer",
            "secret reasoning" not in answer,
        )
        check(
            "thinkingBudget 0 sent to suppress hidden reasoning",
            _t_payloads
            and _t_payloads[0]["generationConfig"].get("thinkingConfig", {}).get("thinkingBudget") == 0,
        )

    print("== Request rejection (400) escalation ==")
    # The founder's SECOND live run: 3.x models 400-reject thinkingBudget 0
    # with the GENERIC message "Request contains an invalid argument." —
    # no field name, so error-text sniffing is impossible. The client must
    # escalate deterministically: thinkingBudget 0 -> thinkingLevel low ->
    # bare request, and only call a BARE 400 fatal.
    _generic_400 = '{"error": {"code": 400, "message": "Request contains an invalid argument.", "status": "INVALID_ARGUMENT"}}'

    # Case A: a 3.x model that accepts thinkingLevel but rejects thinkingBudget.
    _r_calls = []

    def _fake_post_reject(url, headers=None, json=None, timeout=None):
        gen = json["generationConfig"]
        _r_calls.append(dict(gen))
        resp = _mock.Mock()
        thinking = gen.get("thinkingConfig", {})
        if "thinkingBudget" in thinking:
            resp.status_code = 400
            resp.text = _generic_400  # generic: never names the field
        else:
            resp.status_code = 200
            resp.json = lambda: {"candidates": [{"content": {"parts": [{"text": "level ok"}]}}]}
        return resp

    _gapi._config_cache.clear()
    with _mock.patch.object(_gapi.requests, "post", _fake_post_reject), \
         _mock.patch.object(_gapi.requests, "get", _fake_get_ladder), \
         _mock.patch.object(_gapi.time, "sleep", lambda s: None):
        answer = _gapi.complete("hi", max_output_tokens=10)
        check("generic 400 escalates thinkingBudget -> thinkingLevel", answer == "level ok")
        check(
            "escalation switched to thinkingLevel",
            len(_r_calls) >= 2
            and _r_calls[1].get("thinkingConfig", {}).get("thinkingLevel") == "low",
        )
        # The accepted config is remembered: the next call must NOT waste
        # a rejected request on thinkingBudget again.
        _r_calls.clear()
        answer2 = _gapi.complete("hi again", max_output_tokens=10)
        check(
            "accepted config cached per model (no repeat rejection)",
            answer2 == "level ok"
            and _r_calls
            and "thinkingLevel" in _r_calls[0].get("thinkingConfig", {}),
        )

    # Case B: a model that rejects EVERY thinking field — must fall back to
    # a bare request with headroom added so thinking can't starve the answer.
    _b_calls = []

    def _fake_post_bare(url, headers=None, json=None, timeout=None):
        gen = json["generationConfig"]
        _b_calls.append(dict(gen))
        resp = _mock.Mock()
        if "thinkingConfig" in gen:
            resp.status_code = 400
            resp.text = _generic_400
        else:
            resp.status_code = 200
            resp.json = lambda: {"candidates": [{"content": {"parts": [{"text": "bare ok"}]}}]}
        return resp

    _gapi._config_cache.clear()
    with _mock.patch.object(_gapi.requests, "post", _fake_post_bare), \
         _mock.patch.object(_gapi.requests, "get", _fake_get_ladder), \
         _mock.patch.object(_gapi.time, "sleep", lambda s: None):
        answer = _gapi.complete("hi", max_output_tokens=10)
        check("every thinking field rejected -> bare request succeeds", answer == "bare ok")
        check(
            "bare request adds thinking headroom to the budget",
            _b_calls
            and _b_calls[-1]["maxOutputTokens"] == 10 + _gapi.THINKING_HEADROOM_TOKENS,
        )

    # Case C: a bare request that STILL 400s is genuinely broken — fatal,
    # never an infinite retry loop.
    _f_count = []

    def _fake_post_fatal(url, headers=None, json=None, timeout=None):
        _f_count.append(1)
        resp = _mock.Mock()
        resp.status_code = 400
        resp.text = _generic_400
        return resp

    _gapi._config_cache.clear()
    with _mock.patch.object(_gapi.requests, "post", _fake_post_fatal), \
         _mock.patch.object(_gapi.requests, "get", _fake_get_ladder), \
         _mock.patch.object(_gapi.time, "sleep", lambda s: None):
        answer = _gapi.complete("hi", max_output_tokens=10)
        check("bare 400 is fatal (returns empty, no infinite loop)", answer == "")
        check(
            "fatal 400 bounded to the escalation ladder length",
            len(_f_count) <= len(_gapi._THINKING_CONFIGS),
        )

    _gapi._config_cache.clear()
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