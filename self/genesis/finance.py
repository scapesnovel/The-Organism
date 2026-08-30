"""Financial tracking and crypto wallet setup (editable).

Records every income, expense and the amount owed to the founder ("rent").
At the Foundation stage the organism generates its own Ethereum wallet and
records the public address. All records are encrypted at rest.
"""

from __future__ import annotations

import logging
from typing import Optional

from core import config
from core.memory import MemoryManager

LOGGER = logging.getLogger("organism.finance")

RENT_SHARE = 0.10  # 10% of net profit owed to the founder
MIN_BALANCE_NOTICE = 0.0  # USD-equivalent floor before the founder is alerted


def record_income(memory_manager: MemoryManager, source: str, amount: float, currency: str = "USD", txid: str = "") -> None:
    """Record an income event in finance/income.md (encrypted)."""
    entry = (
        f"Income: {amount:.6f} {currency} from {source}"
        + (f" (txid: {txid})" if txid else "")
    )
    memory_manager.append("finance/income.md", entry)
    memory_manager.record_experience(entry)
    _update_balance(memory_manager)
    LOGGER.info("Income recorded: %s %s from %s", amount, currency, source)


def record_expense(memory_manager: MemoryManager, reason: str, amount: float, currency: str = "USD") -> None:
    """Record an expense event in finance/expenses.md (encrypted)."""
    entry = f"Expense: {amount:.6f} {currency} for {reason}"
    memory_manager.append("finance/expenses.md", entry)
    memory_manager.record_experience(entry)
    _update_balance(memory_manager)
    LOGGER.info("Expense recorded: %s %s for %s", amount, currency, reason)


def _total(memory_manager: MemoryManager, rel: str, keyword: str) -> float:
    content = memory_manager.read(rel)
    total = 0.0
    for line in content.splitlines():
        lower = line.lower()
        if keyword not in lower:
            continue
        try:
            rest = line[lower.index(keyword) + len(keyword):]
            total += float(rest.split()[0])
        except (ValueError, IndexError):
            continue
    return total


def update_owed_to_creator(memory_manager: MemoryManager) -> None:
    """Recompute the 'rent' owed to the founder (10% of net profit)."""
    income = _total(memory_manager, "finance/income.md", "income:")
    expenses = _total(memory_manager, "finance/expenses.md", "expense:")
    net = income - expenses
    owed = max(0.0, net * RENT_SHARE)
    content = (
        "# Owed to the founder\n\n"
        f"- rent_share: {RENT_SHARE:.0%}\n"
        f"- total_income: {income:.6f}\n"
        f"- total_expenses: {expenses:.6f}\n"
        f"- net_profit: {net:.6f}\n"
        f"- owed_to_creator: {owed:.6f}\n"
    )
    memory_manager.write("finance/owed_to_creator.md", content)


def _update_balance(memory_manager: MemoryManager) -> None:
    income = _total(memory_manager, "finance/income.md", "income:")
    expenses = _total(memory_manager, "finance/expenses.md", "expense:")
    balance = income - expenses
    identity = memory_manager.read_identity()
    wallet = identity.get("wallet_address", "not yet set")
    content = (
        "# Balance\n\n"
        f"- balance_usd_equivalent: {balance:.6f}\n"
        f"- wallet_address: {wallet}\n"
        f"- updated: {config.utc_now_iso()}\n"
    )
    memory_manager.write("finance/balance.md", content)
    update_owed_to_creator(memory_manager)


def financial_summary(memory_manager: MemoryManager) -> str:
    """Return a one-paragraph financial summary for the daily report."""
    income = _total(memory_manager, "finance/income.md", "income:")
    expenses = _total(memory_manager, "finance/expenses.md", "expense:")
    owed = _total(memory_manager, "finance/owed_to_creator.md", "owed_to_creator:")
    wallet = memory_manager.read_identity().get("wallet_address", "not set")
    return (
        f"Finance: income={income:.6f}, expenses={expenses:.6f}, "
        f"net={income - expenses:.6f}, owed to founder={owed:.6f}. "
        f"Wallet: {wallet}."
    )


def ensure_wallet(memory_manager: MemoryManager) -> None:
    """Generate an Ethereum wallet at the Foundation stage if none exists.

    SECURITY: the private key is NEVER logged or written to disk in
    plaintext (run logs are public on public repositories and persist
    indefinitely). The key is handed over by, in order of preference:

    1. Writing the ORGANISM_WALLET_KEY repository secret directly via the
       GitHub API (requires a GH_TOKEN PAT with secrets scope).
    2. Committing the key encrypted to the founder's PGP public key.

    When neither channel is available, wallet creation is DEFERRED — an
    unrecoverable or leaked wallet is worse than no wallet.
    """
    identity = memory_manager.read_identity()
    if identity.get("wallet_address"):
        return
    try:
        from eth_account import Account
    except ImportError:
        LOGGER.warning(
            "web3/eth-account not installed; wallet generation deferred. "
            "Install requirements.txt extras when ready."
        )
        return

    account = Account.create()
    address = account.address
    private_key = account.key.hex()

    delivered = False
    # Channel 1: store the key as a repository secret via the API.
    try:
        from integrations.github_api import GitHubClient

        gh = GitHubClient()
        if gh.create_or_update_secret("ORGANISM_WALLET_KEY", private_key):
            delivered = True
            LOGGER.info("Wallet key stored directly in the ORGANISM_WALLET_KEY secret.")
    except Exception as exc:
        LOGGER.warning("Could not store wallet key as a secret: %s", exc)

    # Channel 2: encrypted handover file for the founder.
    if not delivered:
        try:
            from core.encryption import encrypt_payload_for_founder

            blob = encrypt_payload_for_founder(private_key)
            backup = config.REPO_ROOT / "secrets" / "wallet_key_for_founder.asc"
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(blob, encoding="utf-8")
            delivered = True
            LOGGER.info(
                "Wallet key written encrypted-to-founder at "
                "secrets/wallet_key_for_founder.asc. Decrypt it, set the "
                "ORGANISM_WALLET_KEY secret, then delete the file."
            )
        except Exception as exc:
            LOGGER.warning("Could not write encrypted wallet key handover: %s", exc)

    if not delivered:
        LOGGER.warning(
            "No safe handover channel for the wallet private key (need a "
            "GH_TOKEN with secrets scope or FOUNDER_PUBLIC_KEY). Wallet "
            "creation deferred; will retry next cycle."
        )
        return

    record = memory_manager.read("memory/core/identity.md")
    record = record.rstrip() + f"\nwallet_address: {address}\n"
    memory_manager.write("memory/core/identity.md", record)
    memory_manager.record_decision(
        f"Ethereum wallet generated: {address}. Private key delivered via a "
        "secure channel (never logged, never stored in plaintext)."
    )
    memory_manager.update_world_state("wallet", address)