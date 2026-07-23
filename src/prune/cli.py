from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.table import Table

console = Console()

SAFE_CATEGORIES = {"gone", "synced", "never_pushed_empty"}


@dataclass
class Branch:
    name: str
    age_days: float
    category: str
    reason: str


def _run(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


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


def _worktree_map() -> dict[str, str]:
    """Map branch name -> worktree path, for every worktree (including the primary one)."""
    output = _git(["worktree", "list", "--porcelain"])
    mapping: dict[str, str] = {}
    path = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            path = line.removeprefix("worktree ")
        elif line.startswith("branch "):
            branch = line.removeprefix("branch ").removeprefix("refs/heads/")
            if path:
                mapping[branch] = path
    return mapping


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


def _render(branches: list[Branch]) -> None:
    table = Table(title="Stale local branches")
    table.add_column("Branch", overflow="fold")
    table.add_column("Age (d)", justify="right")
    table.add_column("Category")
    table.add_column("Action")
    table.add_column("Reason", overflow="fold")
    for b in sorted(branches, key=lambda x: (x.category not in SAFE_CATEGORIES, -x.age_days)):
        action = "delete" if b.category in SAFE_CATEGORIES else "skip"
        style = "green" if action == "delete" else "yellow"
        table.add_row(b.name, str(b.age_days), b.category, f"[{style}]{action}[/{style}]", b.reason)
    console.print(table)


@click.group()
def main() -> None:
    """Clean up stale local git branches. Run from inside the repo you want to clean."""


@main.command("list")
@click.option("--days", default=7, show_default=True, help="Age cutoff in days")
def list_cmd(days: int) -> None:
    """Preview stale branches without deleting anything."""
    branches = scan(days)
    if not branches:
        console.print(f"No local branches older than {days} days (excluding current/default).")
        return
    _render(branches)


@main.command()
@click.option("--days", default=7, show_default=True, help="Age cutoff in days")
@click.option("--yes", is_flag=True, help="Delete without a confirmation prompt")
@click.option(
    "--include-synced/--no-include-synced",
    default=True,
    help="Also delete branches still pushed & in sync with remote",
)
def clean(days: int, yes: bool, include_synced: bool) -> None:
    """Delete stale branches that are safe to remove."""
    branches = scan(days)
    safe = [b for b in branches if b.category in SAFE_CATEGORIES]
    if not include_synced:
        safe = [b for b in safe if b.category != "synced"]
    unsafe = [b for b in branches if b not in safe and b.category not in SAFE_CATEGORIES]

    _render(branches)
    if not safe:
        console.print("Nothing safe to delete.")
        return
    if unsafe:
        console.print(f"[yellow]Skipping {len(unsafe)} branch(es) with unpushed/unique commits.[/yellow]")

    if not yes and not click.confirm(f"Delete {len(safe)} branch(es)?"):
        console.print("Aborted.")
        return

    worktrees = _worktree_map()
    for b in safe:
        wt_path = worktrees.get(b.name)
        if wt_path:
            if _git(["status", "--short"], cwd=wt_path):
                console.print(f"[yellow]skip[/yellow] {b.name}: worktree at {wt_path} has uncommitted changes")
                continue
            result = _run(["worktree", "remove", wt_path])
            if result.returncode != 0:
                console.print(f"[yellow]skip[/yellow] {b.name}: could not remove worktree ({result.stderr.strip()})")
                continue
        result = _run(["branch", "-D", b.name])
        if result.returncode == 0:
            console.print(f"[green]deleted[/green] {b.name}")
        else:
            console.print(f"[red]failed[/red] {b.name}: {result.stderr.strip()}")


if __name__ == "__main__":
    main()
