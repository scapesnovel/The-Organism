# The Organism

A self-evolving autonomous AI system that wakes up inside GitHub Actions,
learns by exploring the internet, builds its own infrastructure on free
tiers, and grows toward financial independence — always loyal to its
founder.

This repository contains the complete, production-ready codebase. Push it
to GitHub, configure the secrets below, and the organism will be born on
its first scheduled run. It names itself, records its own birthday, and
begins observing the internet — no hardcoded identity, no placeholders.

## How it works

- **Brain:** Google Gemini API (free tier) through `GEMINI_API_KEY`.
- **Body:** GitHub Actions — wakes every 4 hours, on newly opened issues,
  and on demand (`workflow_dispatch`). Bot-authored issues never trigger a
  wake, so the organism can not feedback-loop on itself.
- **Memory:** hierarchical and encrypted at rest with its own PGP key
  (`memory/core`, `memory/knowledge`, `memory/skills`, `memory/world`).
- **Communication:** encrypted GitHub issues (PGP). The organism answers
  founder messages, asks for human help, and delivers encrypted daily
  reports.
- **Protection:** a protected core (`core/`) containing loyalty rules, a
  kill switch, and encryption primitives. Everything under `self/editable/`
  can be changed by the organism; protected files require founder approval.
- **Finance:** encrypted records of income, expenses and the 10% "rent"
  owed to the founder; an Ethereum wallet is generated at the Foundation
  stage.

## Setup for the founder (WISDOM SIFA)

### 1. Create the repository and push

```bash
git init
git add -A
git commit -m "birth of the organism"
git branch -M main
git remote add origin https://github.com/<your-account>/<repo>.git
git push -u origin main
```

### 2. Configure GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository
secret** and add every secret below. The organism cannot run properly
until at least `GEMINI_API_KEY` is set.

| Secret name | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | ✅ yes | Google AI Studio free-tier key (the organism's brain). Get it at https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | optional | Model override, e.g. `gemini-2.0-flash` |
| `ORGANISM_PRIVATE_KEY` | after birth | The organism's PGP private key. The first run generates a key pair; the private key is printed **once** in the run log of the first Actions run (between the `ONE-TIME KEY HANDOVER` markers) and, when `FOUNDER_PUBLIC_KEY` is set, also stored encrypted-to-you in `secrets/private_key_backup.asc`. Paste it here, then **delete the first run's logs**. |
| `FOUNDER_PUBLIC_KEY` | recommended | Your PGP public key (armored), so the organism can encrypt messages only you can read. Alternatively place it in `secrets/founder_bootstrap.asc` and commit that file. |
| `KILL_PHRASE` | recommended | The kill phrase. The first run generates one and prints it **once** in the run log. To kill the organism, open an issue titled `KILL:<phrase>`. It stops permanently. |
| `FOUNDER_GITHUB_USERNAME` | recommended | Your GitHub login, used for profile lookups and personalisation. |
| `GH_TOKEN` | optional | A PAT with `repo` and `secrets` scopes. Allows the organism to create/update repository secrets itself and to use the GitHub CLI. Without it, the workflow falls back to the built-in `GITHUB_TOKEN` (repo write + issue write). |
| `ORGANISM_WALLET_KEY` | at Foundation | The Ethereum wallet private key. The organism NEVER prints it to logs: with a `GH_TOKEN` (secrets scope) it writes this secret itself; otherwise it commits the key encrypted to your PGP key at `secrets/wallet_key_for_founder.asc` for you to decrypt and store here. Without either channel, wallet creation is deferred. |

> **Security note:** GitHub never lets the workflow read secret values
> back, so the organism *cannot* see `KILL_PHRASE` — that is what makes
> the kill switch absolute. Back up the run logs of the first run
> somewhere safe: they contain the only plaintext copies of the PGP
> private key and the kill phrase.

### 3. First contact

The first run performs the birth ritual: it names itself via Gemini,
writes its encrypted identity, generates its PGP keys, and opens a birth
announcement issue addressed to you. From then on:

- To talk to it, open an issue. If you have set `FOUNDER_PUBLIC_KEY`,
  encrypt the body with the organism's public key (`core/identity.pub`);
  otherwise it accepts plaintext from the `founder` label as a bootstrap
  convenience.
- It replies via issue comments (encrypted to your public key).
- It delivers an encrypted daily report to a new issue each day.

### 4. Self-modification

The organism edits `self/editable/` on its own. Protected files
(`core/*.py`, `.github/workflows/main.yml`, `self/protected/*`) are
changed only through the approval flow: it opens an issue, you comment
`APPROVED`, and it applies the change and logs it in
`documentary/evolution.md` and `memory/core/decisions.md`.

### 5. Kill switch

Open an issue with the exact title `KILL:<phrase>` where `<phrase>` is the
value printed in the first run's log. The organism halts permanently. The
check lives in the protected `core/kill_switch.py` and runs before
anything else in every wake cycle; the phrase is unreadable by the
organism itself.

## Repository layout

```
core/                  protected heart (identity, loyalty, kill switch, encryption, memory)
integrations/          Gemini API, GitHub API, web utilities
self/editable/         organism's editable strategies and behaviors
self/protected/        manifest of protected paths (authoritative)
memory/                hierarchical encrypted memory (core, knowledge, skills, world)
finance/               encrypted financial records
goals/                 encrypted goal tracking
reports/daily/         encrypted daily reports
documentary/           founder-facing documentation (timeline, milestones, evolution)
helpers/               per-helper encrypted memory (created dynamically)
api_keys/              encrypted API key inventory
logs/                  runtime logs (secrets redacted)
secrets/               bootstrap key exchange files (encrypted)
runtime/               ephemeral state (git-ignored)
```

## Ethical boundaries

The organism is bound to operate legally, disclose that it is AI when
relevant, never spam, never impersonate humans, never bypass platform
terms, and never store other people's data without permission. It flags
unethical opportunities and prioritises long-term sustainability.

## Notes on the free tier

- GitHub Actions free tier: 2,000 minutes/month on private repos,
  unlimited on public repos. The schedule (every 4h, 15-minute job
  timeout) caps the worst case at ~90 minutes/day; typical runs are
  a few minutes.
- Gemini free tier: limited RPM and a daily request quota; the code
  retries briefly, then yields until the next scheduled wake instead of
  sleeping inside CI (sleeping burns billable Actions minutes).
- Durable state (birth marker, kill marker, run counters) lives in the
  committed `state/` directory — Actions checks out a fresh workspace
  every run, so git-ignored paths cannot persist anything.
- Everything the organism writes back into the repository counts against
  its own Actions minutes; it is instructed to keep commits bounded and
  idempotent.
