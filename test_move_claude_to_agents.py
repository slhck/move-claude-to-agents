from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("move_claude_to_agents.py")


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def create_repo(path: Path, *, branch: str = "master") -> Path:
    path.mkdir()
    run("git", "init", "--quiet", f"--initial-branch={branch}", cwd=path)
    run("git", "config", "user.name", "Test User", cwd=path)
    run("git", "config", "user.email", "test@example.com", cwd=path)
    claude_path = path / "CLAUDE.md"
    claude_path.write_text("committed\n", encoding="utf-8")
    run("git", "add", "CLAUDE.md", cwd=path)
    run("git", "commit", "--quiet", "-m", "initial", cwd=path)
    return claude_path


class MigrationDiscoveryTest(unittest.TestCase):
    def test_dry_run_only_lists_committed_files_in_clean_main_repositories(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            search_root = Path(temporary_directory)
            master_repo = search_root / "master-repo"
            master_claude = create_repo(master_repo)
            main_repo = search_root / "main-repo"
            main_claude = create_repo(main_repo, branch="main")

            (master_repo / ".gitignore").write_text(
                "node_modules/\n",
                encoding="utf-8",
            )
            run("git", "add", ".gitignore", cwd=master_repo)
            run(
                "git",
                "commit",
                "--quiet",
                "-m",
                "ignore dependencies",
                cwd=master_repo,
            )

            ignored = master_repo / "node_modules" / "dependency" / "CLAUDE.md"
            ignored.parent.mkdir(parents=True)
            ignored.write_text("ignored\n", encoding="utf-8")

            result = run(
                str(SCRIPT),
                "--auto-commit",
                "--dry-run",
                str(search_root),
                cwd=search_root,
            )

            self.assertIn(f"would migrate: {master_claude}", result.stdout)
            self.assertIn(f"would migrate: {main_claude}", result.stdout)
            self.assertIn(f"would commit: {master_repo}", result.stdout)
            self.assertIn(f"would commit: {main_repo}", result.stdout)
            self.assertNotIn(str(ignored), result.stdout + result.stderr)

    def test_dirty_repository_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo = Path(temporary_directory) / "repo"
            claude_path = create_repo(repo)
            (repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")

            result = run(str(SCRIPT), str(repo), cwd=repo)

            self.assertIn(
                f"warning: skipping {repo}: working tree is not clean",
                result.stderr,
            )
            self.assertEqual("committed\n", claude_path.read_text(encoding="utf-8"))
            self.assertFalse((repo / "AGENTS.md").exists())

    def test_non_main_repository_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo = Path(temporary_directory) / "repo"
            claude_path = create_repo(repo, branch="feature")

            result = run(str(SCRIPT), str(repo), cwd=repo)

            self.assertIn(
                f"warning: skipping {repo}: on branch 'feature', "
                "expected 'main' or 'master'",
                result.stderr,
            )
            self.assertEqual("committed\n", claude_path.read_text(encoding="utf-8"))
            self.assertFalse((repo / "AGENTS.md").exists())


if __name__ == "__main__":
    unittest.main()
