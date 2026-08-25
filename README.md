# prune

Clean up stale local git branches and worktrees — across any repo, safely.

## Problem

Local git branches pile up over time: PRs get merged and the remote branch is deleted, but the
local branch sticks around. `prune` finds branches that are safe to delete (already merged/gone
on the remote, still in sync with the remote, or never pushed and containing no unique commits)
and removes them — without ever touching a branch that has commits you haven't pushed anywhere.

Worktrees leak the same way, and two kinds escape the branch pass entirely: a registration whose
directory you deleted by hand, and a detached checkout that no branch keeps alive. `prune` reports
both and cleans them up.

## Install

```
uv tool install --editable C:/Repos/Personal/prune
```

## Usage

Run from inside any git repo (operates on that repo's branches):

```
prune list              # preview stale branches and worktrees (default: older than 7 days)
prune list --days 14    # change the age cutoff

prune clean             # delete the safe ones (prompts for confirmation)
prune clean --yes       # skip the confirmation prompt
prune clean --no-include-synced   # only delete remote-deleted/never-pushed branches
```

`list` prints two tables: stale branches, then stale worktrees. Each row says `delete` or `skip`
and gives the reason.

## Safety rules

- Never touches the currently checked-out branch or the repo's default branch (`main`/`master`).
- Never deletes a branch with commits ahead of its upstream (unpushed work).
- A never-pushed branch is only deleted if it has zero diff vs the default branch.
- If a stale branch is checked out in one or more `git worktree`s, every worktree is removed first
  (only if clean); a dirty or locked worktree is skipped with a warning, and the branch is left
  alone.
- A worktree that still points at a branch is reported in the branch table only, so nothing is
  counted or deleted twice.
- A detached worktree is only removed when it is clean and older than the cutoff. A recent one is
  never reported.
- A worktree whose directory no longer exists is always safe: only the leftover git metadata is
  removed.

## Why not just `git branch -d`?

`-d`/`-D` don't know about your remote's state or worktrees. `prune` checks push/merge status
live via `git for-each-ref` and diffs against the default branch before deleting anything, and
handles worktree-checked-out branches instead of just failing outright. `git worktree prune` only
clears missing directories; it leaves abandoned detached checkouts in place.
