# Proposed workflow update (apply manually)

The corrected `main.yml` in this folder could not be pushed to
`.github/workflows/` directly because the automation token lacks the
`workflows` permission (a GitHub safety rule).

**To apply it (required for the free-tier and anti-loop fixes):**

```bash
cp .github/workflows-proposed/main.yml .github/workflows/main.yml
git add .github/workflows/main.yml
git commit -m "chore: apply corrected organism workflow"
git push
```

What it changes vs. the current workflow:
- schedule every 4h (was 2h) with a 15-minute timeout (was 55) —
  fits comfortably inside the Actions free tier
- triggers only on *newly opened* issues; `issue_comment` and issue
  *edited/reopened* triggers removed (they created an infinite loop with
  the organism's own comments)
- job-level guard skipping bot-authored issue events
- gnupg apt-get install removed (preinstalled on ubuntu-latest)
- `git pull --rebase` + retries before pushing memory commits
- failure-notification issue is de-duplicated
