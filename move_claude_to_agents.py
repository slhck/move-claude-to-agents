#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "rich>=13.7",
# ]
# ///
"""Migrate committed CLAUDE.md files in clean main or master repositories."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import TypeVar

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.text import Text


COMMIT_MESSAGE = "chore: move CLAUDE.md to AGENTS.md"
T = TypeVar("T")
console = Console()
error_console = Console(stderr=True, soft_wrap=True)


def report(level: str, message: str, *, error: bool = False) -> None:
    """Print a consistently styled, readable status message."""
    styles = {
        "info": "cyan",
        "success": "green",
        "warning": "yellow",
        "error": "bold red",
        "dry run": "magenta",
    }
    output = error_console if error else console
    output.print(Text.assemble((f"{level}: ", styles[level]), message))


def with_progress(items: list[T], description: str) -> Iterator[T]:
    """Yield items while showing a live progress bar on interactive terminals."""
    with Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
        disable=not console.is_terminal,
    ) as progress:
        task = progress.add_task(description, total=len(items))
        for item in items:
            yield item
            progress.advance(task)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "In clean Git repositories on main or master, recursively copy "
            "committed CLAUDE.md files to AGENTS.md and replace each migrated "
            "CLAUDE.md with an @AGENTS.md reference."
        )
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path.cwd(),
        help="file or directory to search from (default: current directory)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing AGENTS.md files",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="show what would be done without making changes",
    )
    parser.add_argument(
        "--auto-commit",
        action="store_true",
        help="commit migrated files in their nearest Git repository",
    )
    parser.add_argument(
        "--auto-push",
        action="store_true",
        help="commit, pull with rebase, and push each affected repository",
    )
    return parser.parse_args()


def find_claude_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.name == "CLAUDE.md" else []
    return sorted(path.rglob("CLAUDE.md"))


def migrate(claude_path: Path, *, force: bool, dry_run: bool) -> bool:
    agents_path = claude_path.with_name("AGENTS.md")

    if agents_path.exists() and not force:
        report(
            "warning",
            f"{agents_path} already exists; skipping {claude_path}",
            error=True,
        )
        return False

    if dry_run:
        report("dry run", f"would migrate: {claude_path} -> {agents_path}")
        return True

    shutil.copy2(claude_path, agents_path)
    claude_path.write_text("@AGENTS.md", encoding="utf-8")
    report("success", f"migrated: {claude_path} -> {agents_path}")
    return True


def find_git_repo(path: Path) -> Path | None:
    for directory in (path.parent, *path.parent.parents):
        if (directory / ".git").exists():
            return directory
    return None


def is_committed(repo: Path, path: Path) -> bool:
    """Return whether path exists in the repository's current HEAD."""
    relative_path = path.relative_to(repo).as_posix()
    result = run_git(repo, "cat-file", "-e", f"HEAD:{relative_path}")
    if result is None:
        return False
    if result.returncode == 0:
        return True
    if result.returncode != 128:
        warn_git_failure(
            repo,
            f"check whether {relative_path} is committed",
            result.stderr,
        )
    return False


def warn_git_failure(repo: Path, action: str, detail: str) -> None:
    suffix = f": {detail.strip()}" if detail.strip() else ""
    report(
        "warning",
        f"could not {action} in {repo}{suffix}",
        error=True,
    )


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        warn_git_failure(repo, f"run git {' '.join(args)}", str(error))
        return None


def repository_is_safe_to_migrate(repo: Path) -> bool:
    """Return whether repo is clean and checked out on main or master."""
    branch_result = run_git(repo, "branch", "--show-current")
    if branch_result is None:
        return False
    if branch_result.returncode != 0:
        warn_git_failure(repo, "determine current branch", branch_result.stderr)
        return False

    status_result = run_git(repo, "status", "--porcelain")
    if status_result is None:
        return False
    if status_result.returncode != 0:
        warn_git_failure(repo, "check working tree status", status_result.stderr)
        return False

    branch = branch_result.stdout.strip()
    problems: list[str] = []
    if branch not in {"main", "master"}:
        branch_description = f"branch '{branch}'" if branch else "detached HEAD"
        problems.append(f"on {branch_description}, expected 'main' or 'master'")
    if status_result.stdout:
        problems.append("working tree is not clean")

    if problems:
        report(
            "warning",
            f"skipping {repo}: {'; '.join(problems)}",
            error=True,
        )
        return False
    return True


def commit_migrations(repo: Path, changed_files: list[Path]) -> bool:
    relative_paths = sorted(
        {str(path.relative_to(repo)) for path in changed_files}
    )
    add_result = run_git(repo, "add", "--", *relative_paths)
    if add_result is None:
        return False
    if add_result.returncode != 0:
        warn_git_failure(repo, "stage migrated files", add_result.stderr)
        return False

    diff_result = run_git(repo, "diff", "--cached", "--quiet", "--", *relative_paths)
    if diff_result is None:
        return False
    if diff_result.returncode == 0:
        report("info", f"nothing to commit in: {repo}")
        return True
    if diff_result.returncode != 1:
        warn_git_failure(repo, "check staged migrated files", diff_result.stderr)
        return False

    commit_result = run_git(
        repo,
        "commit",
        "--only",
        "-m",
        COMMIT_MESSAGE,
        "--",
        *relative_paths,
    )
    if commit_result is None:
        return False
    if commit_result.returncode != 0:
        warn_git_failure(repo, "commit migrated files", commit_result.stderr)
        return False

    report("success", f"committed: {repo}")
    return True


def push_repo(repo: Path) -> bool:
    pull_result = run_git(repo, "pull", "--rebase")
    if pull_result is None:
        return False
    if pull_result.returncode != 0:
        warn_git_failure(repo, "pull with rebase", pull_result.stderr)
        return False

    push_result = run_git(repo, "push")
    if push_result is None:
        return False
    if push_result.returncode != 0:
        warn_git_failure(repo, "push", push_result.stderr)
        return False

    report("success", f"pushed: {repo}")
    return True


def main() -> int:
    args = parse_args()
    path = args.path.expanduser().resolve()

    if not path.exists():
        report("error", f"path does not exist: {path}", error=True)
        return 2

    console.rule("[bold cyan]CLAUDE.md to AGENTS.md[/]")
    report("info", f"searching from {path}")
    if args.dry_run:
        report("dry run", "no files will be changed")

    migrated_by_repo: dict[Path, list[Path]] = defaultdict(list)
    safe_repositories: dict[Path, bool] = {}
    with console.status("[bold cyan]Discovering CLAUDE.md files...[/]"):
        claude_files = find_claude_files(path)

    if not claude_files:
        report("info", "no CLAUDE.md files found")
        return 0

    report("info", f"found {len(claude_files)} CLAUDE.md file(s)")
    migrated_count = 0
    for claude_path in with_progress(claude_files, "Migrating files"):
        repo = find_git_repo(claude_path)
        if repo is None or not is_committed(repo, claude_path):
            continue
        if repo not in safe_repositories:
            safe_repositories[repo] = repository_is_safe_to_migrate(repo)
        if not safe_repositories[repo]:
            continue
        if not migrate(claude_path, force=args.force, dry_run=args.dry_run):
            continue
        migrated_count += 1

        if args.auto_commit or args.auto_push:
            migrated_by_repo[repo].extend(
                [claude_path, claude_path.with_name("AGENTS.md")]
            )

    repositories = list(migrated_by_repo.items())
    for repo, changed_files in with_progress(repositories, "Finishing repositories"):
        if args.dry_run:
            report("dry run", f"would commit: {repo}")
            if args.auto_push:
                report("dry run", f"would pull with rebase and push: {repo}")
            continue
        if commit_migrations(repo, changed_files) and args.auto_push:
            push_repo(repo)

    if migrated_count:
        action = "planned" if args.dry_run else "completed"
        report("success", f"{action}: {migrated_count} migration(s)")
    else:
        report("info", "no eligible files to migrate")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
