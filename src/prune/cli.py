from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.table import Table

console = Console()

SAFE_CATEGORIES = {"gone", "synced", "never_pushed_empty"}
SAFE_WORKTREE_CATEGORIES = {"prunable", "detached"}


@dataclass
class Branch:
    name: str
    age_days: float
    category: str
    reason: str


@dataclass
class Worktree:
    name: str  # filesystem path; named `name` so _render handles branches and worktrees alike
    branch: str | None  # None when the worktree sits on a detached HEAD
    head: str
    prunable: bool
    age_days: float = 0.0
    category: str = ""
    reason: str = ""


def _run(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    except OSError as exc:  # cwd is gone, e.g. a locked worktree whose directory was deleted
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def _git(args: list[str], cwd: str | None = None) -> str:
    return _run(args, cwd).stdout.strip()


def _current_branch() -> str:
    return _git(["branch", "--show-current"])


def _default_branch() -> str:
    ref = _git(["symbolic-ref", "refs/remotes/origin/HEAD"])
    if ref:
        return ref.rsplit("/", 1)[-1]
    for candidate in ("main", "master"):
        if _run(["rev-parse", "--verify", "--quiet", candidate]).returncode == 0:
            return candidate
    return "main"


def _worktrees() -> list[Worktree]:
    """Every linked worktree. Git always prints the main worktree first, so drop it."""
    entries: list[Worktree] = []
    for line in _git(["worktree", "list", "--porcelain"]).splitlines():
        if line.startswith("worktree "):
            entries.append(Worktree(line.removeprefix("worktree "), None, "", False))
        elif not entries:
            continue
        elif line.startswith("HEAD "):
            entries[-1].head = line.removeprefix("HEAD ")
        elif line.startswith("branch "):
            entries[-1].branch = line.removeprefix("branch ").removeprefix("refs/heads/")
        elif line.startswith("prunable"):
            entries[-1].prunable = True
    return entries[1:]


def _branch_worktrees() -> dict[str, list[str]]:
    """Map branch name -> worktree paths. A branch can be checked out more than once."""
    mapping: dict[str, list[str]] = {}
    for wt in _worktrees():
        if wt.branch:
            mapping.setdefault(wt.branch, []).append(wt.name)
    return mapping


def _remove_worktree(path: str) -> str | None:
    """Remove one worktree. Return an error message, or None on success."""
    if _git(["status", "--short"], cwd=path):
        return f"worktree at {path} has uncommitted changes"
    result = _run(["worktree", "remove", path])
    if result.returncode != 0:
        return f"could not remove worktree at {path} ({result.stderr.strip()})"
    return None


def _diff_is_empty(default_branch: str, branch: str) -> bool:
    result = _run(["diff", "--shortstat", f"{default_branch}...{branch}"])
    if result.returncode != 0:
        return False  # can't verify -> treat as unsafe rather than assume empty
    return result.stdout.strip() == ""


def _categorize(track: str, upstream: str, default_branch: str, name: str) -> tuple[str, str]:
    if track == "[gone]":
        return "gone", "remote branch deleted (merged or abandoned)"
    if "ahead" in track:
        return "ahead_unpushed", "has commits not pushed to remote"
    if not upstream:
        if _diff_is_empty(default_branch, name):
            return "never_pushed_empty", "never pushed, no unique commits"
        return "never_pushed_unique", "never pushed, has unique commits"
    return "synced", "in sync with remote"


def scan(days: int) -> list[Branch]:
    """Scan the repo in the current working directory for stale local branches."""
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    current = _current_branch()
    default_branch = _default_branch()
    exclude = {current, default_branch, "main", "master"}
    raw = _git(
        [
            "for-each-ref",
            "refs/heads/",
            "--format=%(refname:short)|%(committerdate:unix)|%(upstream:short)|%(upstream:track)",
        ]
    )
    branches = []
    for line in raw.splitlines():
        name, ts, upstream, track = line.split("|", 3)
        if name in exclude:
            continue
        commit_ts = int(ts)
        if commit_ts >= cutoff:
            continue
        age_days = round((datetime.now(timezone.utc).timestamp() - commit_ts) / 86400, 1)
        category, reason = _categorize(track, upstream, default_branch, name)
        branches.append(Branch(name, age_days, category, reason))
    return branches


def scan_worktrees(days: int) -> list[Worktree]:
    """Scan for worktrees the branch pass cannot reach: missing directories and detached checkouts."""
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - days * 86400
    stale = []
    for wt in _worktrees():
        ts = _git(["log", "-1", "--format=%ct", wt.head])
        wt.age_days = round((now - int(ts)) / 86400, 1) if ts.isdigit() else 0.0
        if wt.prunable:
            wt.category, wt.reason = "prunable", "registered directory no longer exists"
        elif wt.branch or not ts.isdigit() or int(ts) >= cutoff:
            # Branch-backed worktrees appear in the branch table. An unreadable date is unsafe.
            continue
        elif _git(["status", "--short"], cwd=wt.name):
            wt.category, wt.reason = "detached_dirty", "detached HEAD with uncommitted changes"
        else:
            wt.category, wt.reason = "detached", "detached HEAD, clean, no branch to keep it alive"
        stale.append(wt)
    return stale


def _render(items: list[Branch] | list[Worktree], title: str, column: str, safe: set[str]) -> None:
    table = Table(title=title)
    table.add_column(column, overflow="fold")
    table.add_column("Age (d)", justify="right")
    table.add_column("Category")
    table.add_column("Action")
    table.add_column("Reason", overflow="fold")
    for b in sorted(items, key=lambda x: (x.category not in safe, -x.age_days)):
        action = "delete" if b.category in safe else "skip"
        style = "green" if action == "delete" else "yellow"
        table.add_row(b.name, str(b.age_days), b.category, f"[{style}]{action}[/{style}]", b.reason)
    console.print(table)


@click.group()
def main() -> None:
    """Clean up stale local git branches and worktrees. Run from inside the repo you want to clean."""


@main.command("list")
@click.option("--days", default=7, show_default=True, help="Age cutoff in days")
def list_cmd(days: int) -> None:
    """Preview stale branches and worktrees without deleting anything."""
    branches = scan(days)
    worktrees = scan_worktrees(days)
    if not branches and not worktrees:
        console.print(f"Nothing older than {days} days (excluding current/default).")
        return
    if branches:
        _render(branches, "Stale local branches", "Branch", SAFE_CATEGORIES)
    if worktrees:
        _render(worktrees, "Stale worktrees", "Path", SAFE_WORKTREE_CATEGORIES)


@main.command()
@click.option("--days", default=7, show_default=True, help="Age cutoff in days")
@click.option("--yes", is_flag=True, help="Delete without a confirmation prompt")
@click.option(
    "--include-synced/--no-include-synced",
    default=True,
    help="Also delete branches still pushed & in sync with remote",
)
def clean(days: int, yes: bool, include_synced: bool) -> None:
    """Delete stale branches and worktrees that are safe to remove."""
    branches = scan(days)
    worktrees = scan_worktrees(days)
    safe = [b for b in branches if b.category in SAFE_CATEGORIES]
    if not include_synced:
        safe = [b for b in safe if b.category != "synced"]
    safe_worktrees = [w for w in worktrees if w.category in SAFE_WORKTREE_CATEGORIES]
    unsafe = [b for b in branches if b.category not in SAFE_CATEGORIES]

    if branches:
        _render(branches, "Stale local branches", "Branch", SAFE_CATEGORIES)
    if worktrees:
        _render(worktrees, "Stale worktrees", "Path", SAFE_WORKTREE_CATEGORIES)
    if not safe and not safe_worktrees:
        console.print("Nothing safe to delete.")
        return
    if unsafe:
        console.print(f"[yellow]Skipping {len(unsafe)} branch(es) with unpushed/unique commits.[/yellow]")

    summary = f"{len(safe)} branch(es) and {len(safe_worktrees)} worktree(s)"
    if not yes and not click.confirm(f"Delete {summary}?"):
        console.print("Aborted.")
        return

    if any(w.category == "prunable" for w in safe_worktrees):
        _run(["worktree", "prune"])
    for w in safe_worktrees:
        if w.category == "prunable":
            console.print(f"[green]pruned[/green] {w.name}")
            continue
        error = _remove_worktree(w.name)
        if error:
            console.print(f"[yellow]skip[/yellow] {error}")
        else:
            console.print(f"[green]removed[/green] {w.name}")

    branch_worktrees = _branch_worktrees()
    for b in safe:
        error = None
        for path in branch_worktrees.get(b.name, []):
            error = _remove_worktree(path)
            if error:
                break
        if error:
            console.print(f"[yellow]skip[/yellow] {b.name}: {error}")
            continue
        result = _run(["branch", "-D", b.name])
        if result.returncode == 0:
            console.print(f"[green]deleted[/green] {b.name}")
        else:
            console.print(f"[red]failed[/red] {b.name}: {result.stderr.strip()}")


if __name__ == "__main__":
    main()
