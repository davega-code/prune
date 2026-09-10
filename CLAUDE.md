# prune — Agent Instructions

## What this tool does

`prune` deletes stale local git branches that are safe to remove: merged/remote-deleted, still
in sync with the remote, or never pushed with zero unique commits. It never touches branches
with unpushed work, the current branch, or the default branch.

It also removes stale worktrees that the branch pass cannot reach: registrations whose directory
is gone, and abandoned detached checkouts.

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

- Operates on the cwd's repo, or on every repo found under the cwd if the cwd is not a repo
  itself -- no `--repo` flag, no config file
- Repo discovery walks the directory tree for a `.git` directory; a `.git` file (a worktree
  checkout) is skipped without descending into it, since its branches/worktrees are already
  reached through the main repo it points back to
- Safe-to-delete branch categories: `gone`, `synced`, `never_pushed_empty`
- Always-skip branch categories: `ahead_unpushed`, `never_pushed_unique` — never deleted even
  with `--yes`
- Safe-to-delete worktree categories: `prunable` (directory gone), `detached` (detached HEAD,
  clean, older than the cutoff)
- Always-skip worktree category: `detached_dirty`
- Fail-safe: if the default branch can't be resolved or a diff can't be computed, treat the
  branch as unsafe (skip it) rather than assume it's safe to delete
- A worktree that still points at a branch never appears in the worktree table. The branch pass
  owns it, so nothing is reported or deleted twice
- One branch can be checked out in several worktrees. `clean` removes every one of them before
  it deletes the branch, or `git branch -D` fails
- `clean` handles worktrees before branches, so `git worktree prune` first releases a branch that
  a missing worktree still holds
- Branches checked out in a `git worktree` are handled: worktree removed first if clean, branch
  skipped with a warning if the worktree is dirty or removal fails (e.g. locked file)
- Every git invocation runs with `-c core.longpaths=true` so deeply nested worktree paths (e.g.
  under a `*.worktrees` folder) don't fail with "Filename too long" on Windows

## Adding a new command

Only if it can't be expressed as a flag on `list`/`clean`. Update this file's command table below.

## Commands

| Command | What it does |
|---------|-------------|
| `prune list [--days N]` | Preview stale branches and worktrees (default cutoff: 7 days), categorized, for the cwd repo or every repo under it |
| `prune clean [--days N] [--yes] [--no-include-synced]` | Delete the safe ones, across every repo scanned |

## Installation

```
uv tool install --editable C:/Repos/Personal/prune
```

Public PyPI is the only index `pyproject.toml` knows about, so this works unmodified for anyone
and never risks a 401 for an external contributor. A machine without public PyPI access (e.g.
inside Microsoft's network) opts into the internal `SafetyPlatform` feed purely via environment
variables at install time -- see `README.md` -- with no `pyproject.toml` change.

## Testing manually

```
cd /some/repo/with/stale/branches
prune list
prune clean          # confirm prompt appears
prune clean --yes    # actually deletes
```
