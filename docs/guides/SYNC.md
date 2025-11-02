# Sync guide (fork workflow) — one-liners

This repo is wired for:
- upstream = https://github.com/chainstacklabs/pumpfun-bonkfun-bot (read-only source)
- origin   = git@github.com:CalfCrusher/pumpfun-bonkfun-bot.git (your fork)

Your working branch (example): `custom/real-liquidity-fix`

## Verify remotes (one-time sanity check)

```bash
git remote -v
```

## Update local main from upstream (keep pristine)

```bash
git fetch upstream && git checkout main && git reset --hard upstream/main
```

## Rebase your feature branch on latest upstream

```bash
git checkout custom/real-liquidity-fix && git fetch upstream && git rebase upstream/main
```

If conflicts:

```bash
git add -A && git rebase --continue
```

## Push updated branch to your fork

```bash
git push --force-with-lease
```

## Sync your fork's main with upstream (optional)

```bash
git checkout main && git fetch upstream && git reset --hard upstream/main && git push -f origin main
```

## Start a new feature branch from latest upstream/main

```bash
git fetch upstream && git checkout -B custom/new-feature upstream/main
```

## Quick backup of current work to a new branch

```bash
git checkout -b save/local-WIP && git add -A && git commit -m "WIP" && git push -u origin save/local-WIP
```

## Open PR from your fork branch (manual)

- URL pattern: `https://github.com/CalfCrusher/pumpfun-bonkfun-bot/compare/main...CalfCrusher:pumpfun-bonkfun-bot:custom/real-liquidity-fix?expand=1`
- Or use the link Git prints after pushing a new branch.

---
Tips:
- Prefer rebasing your feature branches on `upstream/main` regularly.
- Keep `main` pristine (tracking upstream) and do work in `custom/*` branches.
