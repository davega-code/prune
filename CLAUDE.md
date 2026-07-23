# prune — Agent Instructions

## What this tool does

`prune` deletes stale local git branches that are safe to remove: merged/remote-deleted, still
in sync with the remote, or never pushed with zero unique commits. It never touches branches
with unpushed work, the current branch, or the default branch.

## ZEN.md principles apply here

- Write as little code as possible to solve the actual problem
- No local state/caching — git is the source of truth, always queried live
- Two commands only (`list`, `clean`). Do not add a third without strong justification
- If a new feature needs >20 lines, question whether it belongs here

## Project layout

```
src/prune/
  __init__.py    version string only
  cli.py         all click commands + git plumbing (single module, no need to split further)
```

## Key invariants

- Always operates on the cwd's repo — no `--repo` flag, no config file
- Safe-to-delete categories: `gone`, `synced`, `never_pushed_empty`
- Always-skip categories: `ahead_unpushed`, `never_pushed_unique` — never deleted even with `--yes`
- Fail-safe: if the default branch can't be resolved or a diff can't be computed, treat the
  branch as unsafe (skip it) rather than assume it's safe to delete
- Branches checked out in a `git worktree` are handled: worktree removed first if clean, branch
  skipped with a warning if the worktree is dirty or removal fails (e.g. long path, locked file)

## Adding a new command

Only if it can't be expressed as a flag on `list`/`clean`. Update this file's command table below.

## Commands

| Command | What it does |
|---------|-------------|
| `prune list [--days N]` | Preview stale branches (default cutoff: 7 days), categorized |
| `prune clean [--days N] [--yes] [--no-include-synced]` | Delete the safe ones |

## Installation

```
uv tool install --editable D:/Repos/Tools/prune
```

## Testing manually

```
cd /some/repo/with/stale/branches
prune list
prune clean          # confirm prompt appears
prune clean --yes    # actually deletes
```
