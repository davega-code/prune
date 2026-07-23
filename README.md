# prune

Clean up stale local git branches — across any repo, safely.

## Problem

Local git branches pile up over time: PRs get merged and the remote branch is deleted, but the
local branch sticks around. `prune` finds branches that are safe to delete (already merged/gone
on the remote, still in sync with the remote, or never pushed and containing no unique commits)
and removes them — without ever touching a branch that has commits you haven't pushed anywhere.

## Install

```
uv tool install --editable D:/Repos/Tools/prune
```

## Usage

Run from inside any git repo (operates on that repo's branches):

```
prune list              # preview stale branches (default: older than 7 days)
prune list --days 14    # change the age cutoff

prune clean             # delete the safe ones (prompts for confirmation)
prune clean --yes       # skip the confirmation prompt
prune clean --no-include-synced   # only delete remote-deleted/never-pushed branches
```

## Safety rules

- Never touches the currently checked-out branch or the repo's default branch (`main`/`master`).
- Never deletes a branch with commits ahead of its upstream (unpushed work).
- A never-pushed branch is only deleted if it has zero diff vs the default branch.
- If a stale branch is checked out in a `git worktree`, the worktree is removed first (only if
  clean); dirty or locked worktrees are skipped with a warning, and the branch is left alone.

## Why not just `git branch -d`?

`-d`/`-D` don't know about your remote's state or worktrees. `prune` checks push/merge status
live via `git for-each-ref` and diffs against the default branch before deleting anything, and
handles worktree-checked-out branches instead of just failing outright.
