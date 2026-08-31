"""PGP encryption and decryption for The Organism (protected core).

Key management rules enforced here:

* The organism's 4096-bit RSA key pair is generated once at birth.
* The PRIVATE key is stored in the GitHub Secrets store
  (``ORGANISM_PRIVATE_KEY``) and is only ever loaded from the environment
  by this module. It is never written to disk in plaintext. At birth the
  key is additionally stored *self-encrypted* in ``secrets/private_key_backup.asc``
  so the founder can decrypt it with his own PGP key later.
* The PUBLIC key lives in ``core/identity.pub`` in the repository.
* The founder's public key is loaded from the ``FOUNDER_PUBLIC_KEY``
  secret (or the bootstrap file ``secrets/founder_bootstrap.asc``).

Both python-gnupg and pgpy are supported. ``gnupg`` is preferred when
available because it is battle-tested; ``pgpy`` is used as a fallback.
"""

from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
from typing import Optional

from . import config

try:  # Preferred engine
    import gnupg

    HAS_GPG = True
except Exception:  # pragma: no cover - depends on the environment
    HAS_GPG = False

try:  # Fallback engine (pure Python)
    import pgpy

    HAS_PGPY = True
except Exception:  # pragma: no cover - depends on the environment
    HAS_PGPY = False


class EncryptionError(RuntimeError):
    """Raised when encryption or decryption fails."""


def sanitize_armor(blob: str) -> str:
    """Repair a PGP armor block that was copied from a hostile source.

    The founder captures keys from GitHub Actions logs, where every line
    carries a timestamp prefix and logger lines can interleave with the
    block (this ACTUALLY corrupted the first live key handover). This
    sanitizer makes import forgiving:

    * strips CR and GitHub Actions timestamp prefixes (2026-...Z )
    * drops interleaved log lines ("| INFO |" etc.) and anything outside
      the BEGIN/END markers
    * re-inserts the mandatory blank line between the armor headers and
      the Base64 body when a paste lost it
    """
    import re

    if not blob:
        return ""
    lines = []
    for raw in blob.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?", "", raw).rstrip()
        if re.search(r"\|\s*(INFO|WARNING|ERROR|DEBUG|CRITICAL)\s*\|", line):
            continue  # interleaved logger line — never part of a key
        lines.append(line)
    text = "\n".join(lines)
    match = re.search(
        r"(-----BEGIN PGP [A-Z ]+-----)(.*?)(-----END PGP [A-Z ]+-----)",
        text,
        re.DOTALL,
    )
    if not match:
        return blob.strip()  # not armored (maybe Base64) — leave for caller
    begin, body, end = match.group(1), match.group(2), match.group(3)
    # Separate armor headers (Key: value) from the Base64 payload and
    # rebuild with the required blank line in between.
    headers, payload = [], []
    for ln in body.split("\n"):
        stripped = ln.strip()
        if not stripped:
            continue
        if not payload and re.match(r"^[A-Za-z-]+: ", stripped):
            headers.append(stripped)
        else:
            payload.append(stripped)
    rebuilt = [begin]
    rebuilt.extend(headers)
    rebuilt.append("")
    rebuilt.extend(payload)
    rebuilt.append(end)
    return "\n".join(rebuilt) + "\n"


def describe_armor(blob: str, expected: str) -> str:
    """Explain in plain words what is wrong with a pasted key.

    ``expected`` is ``"private"`` or ``"public"``. The founder pastes keys
    into GitHub secrets by hand; when an import fails the error must say
    exactly WHAT he pasted so he can fix the copy, not guess. This function
    never includes key material in its output — only shape observations.
    """
    blob = (blob or "").strip()
    if not blob:
        return "the secret is EMPTY (nothing was pasted)"
    first = blob.splitlines()[0].strip() if blob.splitlines() else ""
    begin = "-----BEGIN PGP "
    if begin not in blob:
        return (
            "the pasted text is not a PGP key at all — it has no "
            "'-----BEGIN PGP ...-----' line (it starts with: '%s...'). "
            "Copy the WHOLE armored block including the BEGIN and END lines."
            % first[:40]
        )
    has_priv = "BEGIN PGP PRIVATE KEY BLOCK" in blob
    has_pub = "BEGIN PGP PUBLIC KEY BLOCK" in blob
    has_msg = "BEGIN PGP MESSAGE" in blob
    if expected == "private" and has_pub and not has_priv:
        return (
            "a PUBLIC key was pasted where the PRIVATE key belongs — "
            "ORGANISM_PRIVATE_KEY needs the block that says "
            "'BEGIN PGP PRIVATE KEY BLOCK' (SECRET 1 from the handover)"
        )
    if expected == "public" and has_priv and not has_pub:
        return (
            "a PRIVATE key was pasted where the PUBLIC key belongs — "
            "FOUNDER_PUBLIC_KEY needs the block that says "
            "'BEGIN PGP PUBLIC KEY BLOCK' (never paste a private key here)"
        )
    if has_msg and not (has_priv or has_pub):
        return (
            "an ENCRYPTED MESSAGE block was pasted, not a key — this looks "
            "like ciphertext (BEGIN PGP MESSAGE), which cannot be imported"
        )
    kind = "PRIVATE KEY BLOCK" if expected == "private" else "PUBLIC KEY BLOCK"
    if f"END PGP {kind}" not in blob:
        return (
            "the block has a BEGIN line but no matching END line — the "
            "paste is TRUNCATED; copy all the way through "
            "'-----END PGP %s-----'" % kind
        )
    return (
        "the block looks structurally complete but the key data inside is "
        "corrupt (characters lost or altered in the copy) — re-copy it in "
        "one selection, or use the 'Raw' log view to avoid wrapped lines"
    )


class EncryptionManager:
    """Handles PGP operations for the organism.

    ``workdir`` is a scratch directory used for the GPG home when the
    ``gnupg`` engine is active. It never contains secret material on disk
    beyond what GPG itself keeps in its keyring.
    """

    def __init__(self, workdir: Optional[Path] = None) -> None:
        self.workdir = workdir or (config.RUNTIME_DIR / "gpg_work")
        self.workdir.mkdir(parents=True, exist_ok=True)
        # GPG refuses to operate on a world-readable home directory.
        try:
            os.chmod(self.workdir, 0o700)
        except OSError:
            pass
        self._gpg = None
        self._organism_fingerprint: Optional[str] = None
        self._founder_fingerprint: Optional[str] = None
        # pgpy engine key objects (always initialised so attribute access is safe)
        self._organism_key = None
        self._founder_key = None

    # ------------------------------------------------------------------
    # Engine selection
    # ------------------------------------------------------------------
    @property
    def engine(self) -> str:
        if HAS_GPG:
            return "gnupg"
        if HAS_PGPY:
            return "pgpy"
        return "none"

    def _ensure_engine(self) -> None:
        if not HAS_GPG and not HAS_PGPY:
            raise EncryptionError(
                "No PGP engine available. Install 'python-gnupg' or 'pgpy' "
                "(see requirements.txt)."
            )

    def _get_gpg(self):
        """Lazily create the GPG instance with a dedicated home directory."""
        if self._gpg is None:
            if not HAS_GPG:
                raise EncryptionError("python-gnupg is not installed.")
            import gnupg  # noqa: F401

            self._gpg = gnupg.GPG(gnupghome=str(self.workdir))
            # python-gnupg defaults to latin-1; any non-latin-1 character
            # (em-dashes, emoji, non-English text) would crash encryption.
            self._gpg.encoding = "utf-8"
        return self._gpg

    # ------------------------------------------------------------------
    # Key lifecycle
    # ------------------------------------------------------------------
    def generate_key_pair(self) -> str:
        """Generate a 4096-bit RSA key pair and return the armored PRIVATE key.

        The private key is returned (never logged) and the public key is
        exported to ``core/identity.pub``.
        """
        self._ensure_engine()
        if self.engine == "gnupg":
            gpg = self._get_gpg()
            # Modern GnuPG (>= 2.1) requires the explicit %no-protection
            # directive for a passphrase-less key; passing passphrase=""
            # alone makes gen_key fail against the gpg-agent.
            input_data = gpg.gen_key_input(
                key_type="RSA",
                key_length=4096,
                name_real="The Organism (autonomous entity)",
                name_email="organism@localhost",
                expire_date="0",
                no_protection=True,
            )
            key = gpg.gen_key(input_data)
            if not key or not key.fingerprint:
                raise EncryptionError("GPG key generation failed.")
            self._organism_fingerprint = key.fingerprint
            public_armor = gpg.export_keys(key.fingerprint)
            private_armor = gpg.export_keys(
                key.fingerprint,
                secret=True,
                expect_passphrase=False,
            )
            if not private_armor:
                raise EncryptionError("GPG private key export failed.")
            config.IDENTITY_PUB_FILE.write_text(public_armor, encoding="utf-8")
            return private_armor

        # pgpy engine: the key algorithm must be passed as an enum constant.
        import pgpy  # noqa: F401
        from pgpy.constants import KeyFlags, PubKeyAlgorithm

        key = pgpy.PGPKey.new(PubKeyAlgorithm.RSAEncryptOrSign, 4096)
        uid = pgpy.PGPUID.new("The Organism (autonomous entity)")
        key.add_uid(
            uid,
            usage={
                KeyFlags.Sign,
                KeyFlags.Certify,
                KeyFlags.EncryptCommunications,
                KeyFlags.EncryptStorage,
            },
        )
        private_armor = str(key)
        # Keep the generated key loaded so this run can immediately encrypt.
        self._organism_key = key
        config.IDENTITY_PUB_FILE.write_text(str(key.pubkey), encoding="utf-8")
        return private_armor

    def import_organism_private_key(self, private_armor: str) -> None:
        """Import the organism's own private key from the environment."""
        if not private_armor or not private_armor.strip():
            raise EncryptionError(
                "Could not import organism private key: "
                + describe_armor(private_armor, "private")
            )
        raw = private_armor
        private_armor = sanitize_armor(private_armor)
        if self.engine == "gnupg":
            gpg = self._get_gpg()
            result = gpg.import_keys(private_armor)
            if not result or not result.fingerprints:
                raise EncryptionError(
                    "Could not import organism private key: "
                    + describe_armor(raw, "private")
                )
            self._organism_fingerprint = result.fingerprints[0]
        else:
            import pgpy  # noqa: F401

            try:
                key, _ = pgpy.PGPKey.from_blob(private_armor)
            except Exception:
                raise EncryptionError(
                    "Could not import organism private key: "
                    + describe_armor(raw, "private")
                )
            self._organism_key = key  # type: ignore[attr-defined]

    def import_founder_public_key(self, public_armor: str) -> None:
        """Import the founder's public key (armored or bare Base64)."""
        public_armor = (public_armor or "").strip()
        if not public_armor:
            raise EncryptionError(
                "Could not import founder public key: "
                + describe_armor(public_armor, "public")
            )
        raw = public_armor
        public_armor = sanitize_armor(public_armor)
        # A Base64-only secret is decoded to armored form.
        if not public_armor.startswith("-----BEGIN"):
            try:
                public_armor = base64.b64decode(public_armor.encode()).decode("utf-8")
                public_armor = sanitize_armor(public_armor)
            except (binascii.Error, ValueError):
                pass  # keep the raw string; import will fail with a clear error
        if self.engine == "gnupg":
            gpg = self._get_gpg()
            result = gpg.import_keys(public_armor)
            if not result or not result.fingerprints:
                raise EncryptionError(
                    "Could not import founder public key: "
                    + describe_armor(raw, "public")
                )
            self._founder_fingerprint = result.fingerprints[0]
        else:
            import pgpy  # noqa: F401

            try:
                key, _ = pgpy.PGPKey.from_blob(public_armor)
            except Exception:
                raise EncryptionError(
                    "Could not import founder public key: "
                    + describe_armor(raw, "public")
                )
            self._founder_key = key.pubkey  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Public primitives
    # ------------------------------------------------------------------
    def is_configured(self) -> bool:
        """True when at least the organism's own key is available."""
        return self.has_organism_key()

    def has_organism_key(self) -> bool:
        """True when the organism's own key material is actually loaded.

        This is the authoritative check used by the memory layer to decide
        whether encryption-at-rest is possible. It must reflect *loaded key
        material*, not merely engine availability, otherwise the first run
        (before any key exists) crashes on every encrypted write.
        """
        if self.engine == "gnupg":
            return self._organism_fingerprint is not None
        if self.engine == "pgpy":
            return self._organism_key is not None
        return False

    def has_founder_key(self) -> bool:
        """True when the founder's public key is loaded."""
        if self.engine == "gnupg":
            return self._founder_fingerprint is not None
        if self.engine == "pgpy":
            return self._founder_key is not None
        return False

    def encrypt_to_self(self, plaintext: str) -> str:
        """Encrypt ``plaintext`` so that only the organism can read it."""
        return self._encrypt(plaintext, recipient="self")

    def decrypt_from_self(self, ciphertext: str) -> str:
        """Decrypt ciphertext produced with the organism's public key."""
        return self._decrypt(ciphertext, use_private=True)

    def encrypt_to_founder(self, plaintext: str) -> str:
        """Encrypt ``plaintext`` so that only the founder can read it."""
        return self._encrypt(plaintext, recipient="founder")

    def decrypt_from_founder(self, ciphertext: str) -> str:
        """Decrypt a message the founder encrypted with the organism's public key."""
        return self._decrypt(ciphertext, use_private=True)

    # ------------------------------------------------------------------
    # Engine internals
    # ------------------------------------------------------------------
    def _encrypt(self, plaintext: str, recipient: str) -> str:
        self._ensure_engine()
        if self.engine == "gnupg":
            gpg = self._get_gpg()
            fingerprint = (
                self._organism_fingerprint
                if recipient == "self"
                else self._founder_fingerprint
            )
            if not fingerprint:
                raise EncryptionError(
                    f"No {recipient} PGP key available for encryption."
                )
            result = gpg.encrypt(plaintext, fingerprint, always_trust=True)
            if not result or not str(result):
                raise EncryptionError(
                    f"Encryption to {recipient} failed: {getattr(result, 'stderr', '')}"
                )
            return str(result)

        # pgpy engine
        import pgpy  # noqa: F401

        key = (
            self._organism_key  # type: ignore[attr-defined]
            if recipient == "self"
            else self._founder_key  # type: ignore[attr-defined]
        )
        if key is None:
            raise EncryptionError(f"No {recipient} PGP key available for encryption.")
        # pgpy's encrypt action requires a public-only key; ``pubkey`` is
        # idempotent for keys that are already public.
        message = pgpy.PGPMessage.new(plaintext)
        encrypted = key.pubkey.encrypt(message)
        return str(encrypted)

    def _decrypt(self, ciphertext: str, use_private: bool = True) -> str:
        self._ensure_engine()
        if self.engine == "gnupg":
            gpg = self._get_gpg()
            result = gpg.decrypt(ciphertext)
            if not result or not str(result):
                raise EncryptionError(
                    f"Decryption failed: {getattr(result, 'stderr', 'unknown error')}"
                )
            return str(result)

        # pgpy engine: decryption may return a (message, status) tuple or a
        # context-manager style message depending on the pgpy version.
        import pgpy  # noqa: F401

        message = pgpy.PGPMessage.from_blob(ciphertext)
        decrypted = self._organism_key.decrypt(message)  # type: ignore[attr-defined]
        if isinstance(decrypted, tuple):
            decrypted = decrypted[0]
        text = getattr(decrypted, "message", decrypted)
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        return str(text)

    # ------------------------------------------------------------------
    # Git-friendly payload encoding
    # ------------------------------------------------------------------
    @staticmethod
    def wrap_payload(ciphertext: str) -> str:
        """Wrap PGP armor in a fenced marker so files stay readable in diffs."""
        body = base64.b64encode(ciphertext.encode("utf-8")).decode("ascii")
        return f"-----BEGIN ORGANISM ENCRYPTED DATA-----\n{body}\n-----END ORGANISM ENCRYPTED DATA-----\n"

    @staticmethod
    def unwrap_payload(wrapped: str) -> str:
        """Extract the raw PGP armor from a wrapped payload."""
        marker = "-----BEGIN ORGANISM ENCRYPTED DATA-----"
        end_marker = "-----END ORGANISM ENCRYPTED DATA-----"
        if marker not in wrapped:
            return wrapped
        start = wrapped.index(marker) + len(marker)
        end = wrapped.index(end_marker, start)
        body = wrapped[start:end].strip()
        try:
            return base64.b64decode(body.encode("ascii")).decode("utf-8")
        except (binascii.Error, ValueError) as exc:
            raise EncryptionError(f"Could not unwrap encrypted payload: {exc}") from exc

    # ------------------------------------------------------------------
    # Disk helpers used by the memory layer
    # ------------------------------------------------------------------
    def encrypt_file(self, path: Path, plaintext: str) -> None:
        """Encrypt ``plaintext`` to self and write the wrapped payload to disk."""
        ciphertext = self.encrypt_to_self(plaintext)
        wrapped = self.wrap_payload(ciphertext)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wrapped, encoding="utf-8")

    def decrypt_file(self, path: Path) -> str:
        """Read a wrapped payload and decrypt it back to plaintext."""
        content = path.read_text(encoding="utf-8")
        armor = self.unwrap_payload(content)
        return self.decrypt_from_self(armor)


def self_encrypt_private_key(private_armor: str, founder_public_armor: Optional[str]) -> str:
    """Encrypt the organism's private key TO THE FOUNDER and return the blob.

    Used at birth: the private key must never be stored in plaintext, so it
    is encrypted to the founder's public key.

    NOTE: encrypting the private key to the organism's *own* public key was
    removed because it is circular — you would need the private key to
    decrypt the private key, making such a backup unrecoverable. When the
    founder's key is unavailable this function raises so callers can defer
    the backup instead of writing a useless file.
    """
    if not founder_public_armor:
        raise EncryptionError(
            "Cannot back up the private key: the founder's public key is not "
            "configured. A self-encrypted backup would be circular and "
            "unrecoverable."
        )
    manager = EncryptionManager()
    manager.import_organism_private_key(private_armor)
    manager.import_founder_public_key(founder_public_armor)
    return manager.wrap_payload(manager.encrypt_to_founder(private_armor))


def encrypt_payload_for_founder(plaintext: str) -> str:
    """Encrypt a plaintext message for the founder using the configured key."""
    manager = EncryptionManager()
    founder_armor = os.environ.get(config.ENV_FOUNDER_PUBLIC_KEY, "").strip()
    if founder_armor:
        manager.import_founder_public_key(founder_armor)
    else:
        bootstrap = config.REPO_ROOT / config.FOUNDER_BOOTSTRAP_FILE
        if bootstrap.exists():
            content = bootstrap.read_text(encoding="utf-8")
            try:
                manager.import_founder_public_key(manager.unwrap_payload(content))
            except Exception:
                manager.import_founder_public_key(content)
        else:
            raise EncryptionError(
                "Founder public key is not configured. Set FOUNDER_PUBLIC_KEY "
                "or place the key in secrets/founder_bootstrap.asc"
            )
    return manager.encrypt_to_founder(plaintext)