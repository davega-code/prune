from __future__ import annotations

import os
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


def _find_repos(root: str) -> list[str]:
    """Every git repo under root, root itself included if it is one.

    A worktree checkout has a `.git` file (not a directory) pointing back at its main
    repo, so it is skipped here -- its branches and worktrees are already reachable
    by scanning that main repo, wherever it is found.
    """
    if os.path.isdir(os.path.join(root, ".git")):
        return [root]
    repos = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if os.path.isdir(os.path.join(dirpath, ".git")):
            repos.append(dirpath)
            dirnames[:] = []
        elif os.path.isfile(os.path.join(dirpath, ".git")):
            dirnames[:] = []
    return sorted(repos)


def _current_branch(cwd: str) -> str:
    return _git(["branch", "--show-current"], cwd)


def _default_branch(cwd: str) -> str:
    ref = _git(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd)
    if ref:
        return ref.rsplit("/", 1)[-1]
    for candidate in ("main", "master"):
        if _run(["rev-parse", "--verify", "--quiet", candidate], cwd).returncode == 0:
            return candidate
    return "main"


def _worktrees(cwd: str) -> list[Worktree]:
    """Every linked worktree. Git always prints the main worktree first, so drop it."""
    entries: list[Worktree] = []
    for line in _git(["worktree", "list", "--porcelain"], cwd).splitlines():
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


def _branch_worktrees(cwd: str) -> dict[str, list[str]]:
    """Map branch name -> worktree paths. A branch can be checked out more than once."""
    mapping: dict[str, list[str]] = {}
    for wt in _worktrees(cwd):
        if wt.branch:
            mapping.setdefault(wt.branch, []).append(wt.name)
    return mapping


def _remove_worktree(cwd: str, path: str) -> str | None:
    """Remove one worktree. Return an error message, or None on success."""
    if _git(["status", "--short"], cwd=path):
        return f"worktree at {path} has uncommitted changes"
    result = _run(["worktree", "remove", path], cwd=cwd)
    if result.returncode != 0:
        return f"could not remove worktree at {path} ({result.stderr.strip()})"
    return None


def _diff_is_empty(cwd: str, default_branch: str, branch: str) -> bool:
    result = _run(["diff", "--shortstat", f"{default_branch}...{branch}"], cwd)
    if result.returncode != 0:
        return False  # can't verify -> treat as unsafe rather than assume empty
    return result.stdout.strip() == ""


def _categorize(cwd: str, track: str, upstream: str, default_branch: str, name: str) -> tuple[str, str]:
    if track == "[gone]":
        return "gone", "remote branch deleted (merged or abandoned)"
    if "ahead" in track:
        return "ahead_unpushed", "has commits not pushed to remote"
    if not upstream:
        if _diff_is_empty(cwd, default_branch, name):
            return "never_pushed_empty", "never pushed, no unique commits"
        return "never_pushed_unique", "never pushed, has unique commits"
    return "synced", "in sync with remote"


def scan(cwd: str, days: int) -> list[Branch]:
    """Scan one repo for stale local branches."""
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    current = _current_branch(cwd)
    default_branch = _default_branch(cwd)
    exclude = {current, default_branch, "main", "master"}
    raw = _git(
        [
            "for-each-ref",
            "refs/heads/",
            "--format=%(refname:short)|%(committerdate:unix)|%(upstream:short)|%(upstream:track)",
        ],
        cwd,
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
        category, reason = _categorize(cwd, track, upstream, default_branch, name)
        branches.append(Branch(name, age_days, category, reason))
    return branches


def scan_worktrees(cwd: str, days: int) -> list[Worktree]:
    """Scan one repo for worktrees the branch pass cannot reach: missing directories and detached checkouts."""
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - days * 86400
    stale = []
    for wt in _worktrees(cwd):
        ts = _git(["log", "-1", "--format=%ct", wt.head], cwd)
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


def _label(root: str, repo: str, repo_count: int) -> str:
    return f" — {os.path.relpath(repo, root)}" if repo_count > 1 else ""


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
    """Clean up stale local git branches and worktrees.

    Operates on the git repo in the current directory. If the current directory is
    not itself a repo, every git repo found underneath it is scanned and cleaned
    instead.
    """


@main.command("list")
@click.option("--days", default=7, show_default=True, help="Age cutoff in days")
def list_cmd(days: int) -> None:
    """Preview stale branches and worktrees without deleting anything."""
    root = os.getcwd()
    repos = _find_repos(root)
    if not repos:
        console.print("No git repos found here.")
        return
    found_any = False
    for repo in repos:
        branches = scan(repo, days)
        worktrees = scan_worktrees(repo, days)
        if not branches and not worktrees:
            continue
        found_any = True
        label = _label(root, repo, len(repos))
        if branches:
            _render(branches, f"Stale local branches{label}", "Branch", SAFE_CATEGORIES)
        if worktrees:
            _render(worktrees, f"Stale worktrees{label}", "Path", SAFE_WORKTREE_CATEGORIES)
    if not found_any:
        scope = f" across {len(repos)} repo(s)" if len(repos) > 1 else ""
        console.print(f"Nothing older than {days} days (excluding current/default){scope}.")


def _clean_repo(repo: str, safe: list[Branch], safe_worktrees: list[Worktree]) -> None:
    if any(w.category == "prunable" for w in safe_worktrees):
        _run(["worktree", "prune"], cwd=repo)
    for w in safe_worktrees:
        if w.category == "prunable":
            console.print(f"[green]pruned[/green] {w.name}")
            continue
        error = _remove_worktree(repo, w.name)
        if error:
            console.print(f"[yellow]skip[/yellow] {error}")
        else:
            console.print(f"[green]removed[/green] {w.name}")

    branch_worktrees = _branch_worktrees(repo)
    for b in safe:
        error = None
        for path in branch_worktrees.get(b.name, []):
            error = _remove_worktree(repo, path)
            if error:
                break
        if error:
            console.print(f"[yellow]skip[/yellow] {b.name}: {error}")
            continue
        result = _run(["branch", "-D", b.name], cwd=repo)
        if result.returncode == 0:
            console.print(f"[green]deleted[/green] {b.name}")
        else:
            console.print(f"[red]failed[/red] {b.name}: {result.stderr.strip()}")


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
    root = os.getcwd()
    repos = _find_repos(root)
    if not repos:
        console.print("No git repos found here.")
        return

    plan: list[tuple[str, list[Branch], list[Worktree]]] = []
    total_branches = total_worktrees = total_unsafe = 0
    for repo in repos:
        branches = scan(repo, days)
        worktrees = scan_worktrees(repo, days)
        if not branches and not worktrees:
            continue
        safe = [b for b in branches if b.category in SAFE_CATEGORIES]
        if not include_synced:
            safe = [b for b in safe if b.category != "synced"]
        safe_worktrees = [w for w in worktrees if w.category in SAFE_WORKTREE_CATEGORIES]
        unsafe = [b for b in branches if b.category not in SAFE_CATEGORIES]

        label = _label(root, repo, len(repos))
        if branches:
            _render(branches, f"Stale local branches{label}", "Branch", SAFE_CATEGORIES)
        if worktrees:
            _render(worktrees, f"Stale worktrees{label}", "Path", SAFE_WORKTREE_CATEGORIES)

        plan.append((repo, safe, safe_worktrees))
        total_branches += len(safe)
        total_worktrees += len(safe_worktrees)
        total_unsafe += len(unsafe)

    if total_branches == 0 and total_worktrees == 0:
        console.print("Nothing safe to delete.")
        return
    if total_unsafe:
        console.print(f"[yellow]Skipping {total_unsafe} branch(es) with unpushed/unique commits.[/yellow]")

    summary = f"{total_branches} branch(es) and {total_worktrees} worktree(s)"
    if len(repos) > 1:
        summary += f" across {len({r for r, s, w in plan if s or w})} repo(s)"
    if not yes and not click.confirm(f"Delete {summary}?"):
        console.print("Aborted.")
        return

    for repo, safe, safe_worktrees in plan:
        _clean_repo(repo, safe, safe_worktrees)


if __name__ == "__main__":
    main()
