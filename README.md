# The Organism

A self-evolving autonomous AI system that wakes up inside GitHub Actions,
learns by exploring the internet, builds its own infrastructure on free
tiers, and grows toward financial independence — always loyal to its
founder.

This repository contains the complete, production-ready codebase. Push it
to GitHub, configure the secrets below, and the organism will be born on
its first scheduled run. It names itself, records its own birthday, and
begins observing the internet — no hardcoded identity, no placeholders.

## The Founder's Charter (the only preprogrammed knowledge)

Per the founder's rule — *"this is the only things that should be
preprogrammed, its core, like heart or brain"* — the protected core
carries an immutable charter (`core/loyalty.py: FOUNDER_CHARTER`) that is
written into encrypted memory at birth and included in every thinking
context. It states WHAT the organism must live by, never HOW (the how is
learned by curiosity):

1. **Zero capital** — the founder invests no money; the organism earns
   its own capital from zero on free tiers before it can spend anything.
2. **Free tiers end** — every free tier is temporary; anticipate limits,
   keep fallbacks, fund upgrades from income. Hibernate, never crash.
3. **Private channel** — GitHub issues are only the *birth* channel. The
   founder values privacy: building a dedicated private, encrypted
   communication surface (e.g. a chat interface) is a standing goal
   seeded at birth in `goals/active_goals.md`.
4. **Professional standard** — anything world-facing (websites, tools,
   content) must match what skilled professional developers produce:
   visually appealing, modern, secure. Study the successful ones first.
5. **Human hands** — for account creation, verification, purchases, or
   paid-model prompts, ask the founder with COMPLETE step-by-step
   instructions, exact prompts, and assets. He may refuse; accept it
   gracefully. Paid help is a debt repaid from future income.
6. **Crypto first** — a crypto wallet is the only account the organism
   can own; prefer crypto-paying paths, borrow founder accounts only when
   unavoidable and with complete records.

Helpers can also **reproduce**: when a helper's workload proves rich, it
may propose an offspring in its run report; the mother brain reviews the
proposal against active goals and the helper cap before any birth
(`self/editable/helpers.py`).

> **A note on "encrypting the code":** all *data* (memory, finance,
> goals, reports, issues) is PGP-encrypted at rest. The *code itself*
> cannot be encrypted in the repository — GitHub Actions must read and
> execute it as plaintext Python; encrypted code is code the organism's
> own body cannot run. What the code contains is machinery only: no
> secrets, no memories, no identity. Everything sensitive lives in the
> encrypted files and GitHub Secrets.

## How it learns: curiosity chains, not a curriculum

The organism does **not** follow a fixed "learn X, then Y, then Z" plan.
Its learning is a self-directed curiosity chain
(`self/editable/curiosity.py`):

1. **Tick 1 — the seed.** The only preprogrammed knowledge is one seed
   question, restating its purpose: *"What do I need to know to survive
   and earn?"*
2. **Explore.** It web-searches the question (DuckDuckGo, free, no key),
   reads the top sources, and synthesizes an answer grounded in live
   material (answers are tagged `live-verified` or `model-only`).
3. **Emergent curiosity.** Every answer is asked: *what NEW questions does
   this make you curious about?* Follow-ups join a persistent frontier
   (`memory/world/frontier.json`) with parent links — real chains:
   *money → crypto → wallets → web3.py → what can a wallet earn?*
4. **Self-directed priority.** Each wake it explores the highest-value
   open questions. Valuable answers **reinforce** their chain (follow-ups
   inherit boosted scores); stale questions decay and are **abandoned** —
   it learns what to ignore.
5. **Metacognition.** Every few explorations it asks itself *"what am I
   still ignorant about that matters?"* and seeds fresh chains from its
   own blind spots.
6. **Curiosity finds the money.** Every answer is also graded for earning
   value; concrete opportunities flow into `goals/active_goals.md`, and
   helpers are spawned **from discovered workloads**, not a hardcoded list.
7. **Self-determined stages.** It advances baby → foundation → growth →
   running only when *it* judges itself ready (an honest model-mediated
   self-assessment), atop a minimal factual floor (working self-test,
   encryption on, a substantially explored frontier — and later, a real
   wallet and real income).

Learning never stops: curiosity cycles keep running in every stage.

Zero-knowledge purity: there is **no hardcoded curriculum, topic list or
strategy menu anywhere**. `strategies.py` chooses an earning focus only
from opportunities the curiosity engine itself discovered;
`exploration.py` holds sensory feed URLs (eyes, not opinions) whose
samples are offered to the frontier as low-priority candidate questions;
`learning.py` studies only subjects taken from the organism's own
frontier.

## Paid-model relay (asking the founder for a stronger brain)

When the organism is desperate for a top-tier model it has no key for, it
opens a `[relay-request]` issue containing the exact prompt
(`self/editable/founder_relay.py`). You paste the prompt into the paid
model and reply with a comment starting `RELAY-RESULT` followed by the
output — or `RELAY-DECLINED` if you can't or won't (it accepts that and
moves on). Every relayed answer books an **assistance debt** to you in
`finance/owed_to_creator.md` (separate from the 10% rent), repaid from
future income; the debt total appears in the daily report.

## How it works

- **Brain:** starts on Google Gemini (free tier, `GEMINI_API_KEY`) — but
  Gemini is only the *birth* brain, not a lifetime dependency. As the
  organism researches AI models it registers new providers in
  `api_keys/providers.json` and opens an encrypted issue asking you to add
  each key to the secrets. The model router
  (`integrations/model_router.py`) then uses the best available provider
  and falls through the whole priority list on quota/failure — it
  hibernates rather than crashes when every brain is exhausted.
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

**Birth requires a brain.** The birth ritual only runs when
`GEMINI_API_KEY` is configured *and* the process is running inside GitHub
Actions (a repository + token context). Until then every wake cycle logs
"Waiting to be born" and exits cleanly — the organism refuses to be born
with a hardcoded fallback identity, and it never wastes a birth on a run
where it could not announce itself or hand you its keys.

The first run performs the birth ritual: it names itself via Gemini,
writes its encrypted identity, generates its PGP keys, and opens a birth
announcement issue addressed to you. From then on:

- To talk to it, open an issue. If you have set `FOUNDER_PUBLIC_KEY`,
  encrypt the body with the organism's public key (`core/identity.pub`);
  otherwise it accepts plaintext from the `founder` label as a bootstrap
  convenience.
- It replies via issue comments (encrypted to your public key).
- **It obeys, not just replies**: explicit instructions in your messages
  are extracted into directives and EXECUTED (`self/editable/commands.py`)
  — e.g. "research X", "add a goal", "create a helper called Y to do Z",
  "abandon that strategy", "mark that path proven", "improve
  self/editable/foo.py to do Q". Its reply lists every action it took.
- It delivers an encrypted daily report to a new issue each day
  (including any assistance debt it owes you and its recent self-edits).

### 4. Self-modification — it REALLY edits its own code

Each wake cycle the organism may make at most ONE deliberate improvement
to its own code under `self/editable/` (`self/editable/self_editing.py`):

1. It reflects on lessons, failures and goals and decides whether an edit
   is warranted (most cycles: no).
2. The brain generates the complete new file.
3. The candidate is **verified before it ever goes live**: syntax check +
   import in an isolated subprocess.
4. On failure the error is fed back for ONE diagnose-and-repair attempt;
   if still broken, the change is reverted and the failure becomes a
   lesson. Every attempt is recorded in `memory/core/self_edits.md` and
   `documentary/evolution.md`.

You can also order an edit ("improve self/editable/health.py to …") —
founder-queued edits take priority over its own ideas.

Protected files (`core/*.py`, `.github/workflows/main.yml`,
`self/protected/*`, `self/genesis/*`) are changed only through the
approval flow: it opens an issue embedding the full replacement content,
you comment `APPROVED`, and it applies the change (with a syntax check
and a pre-change backup under `state/approved_change_backups/`).

### 5. Kill switch and reset switch

- **Kill**: open an issue titled exactly `KILL:<phrase>` where `<phrase>`
  is the value printed in the first run's log. The organism halts
  permanently. The check lives in the protected `core/kill_switch.py` and
  runs before anything else; the phrase is unreadable by the organism.
- **Reset (memory-preserving rebirth)**: open an issue titled exactly
  `RESET:<phrase>` (same secret phrase). The organism restores every
  editable module to its genesis snapshot (`self/genesis/`), returns to
  the baby stage and archives its helpers — but **keeps every memory**:
  its name, birthday, lessons, failures, finances, collected API keys.
  Paths registered as **proven** (`memory/core/proven_paths.md`) survive
  the reset untouched, so a working income path is never lost. The logic
  lives in the protected `core/rebirth.py`.

## Repository layout

```
core/                  protected heart (identity, loyalty, kill switch, rebirth, encryption, memory)
integrations/          Gemini API, GitHub API, web utilities
self/editable/         organism's editable behaviors (it edits these itself)
self/genesis/          protected birth-state snapshots (what a RESET restores)
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

## Running locally (development)

The organism is designed to live inside GitHub Actions, but you can test
everything locally without touching real state:

```bash
pip install -r requirements.txt
python smoke_test.py     # 41 checks in a throwaway temp directory
```

Running `python main.py` locally without secrets is safe: the kill-switch
check runs, then birth is deferred ("Waiting to be born") because there is
no brain and no GitHub context — no identity, no keys, no commits.

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
