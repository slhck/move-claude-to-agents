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


class MigrationDiscoveryTest(unittest.TestCase):
    def test_dry_run_only_lists_committed_claude_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            search_root = Path(temporary_directory)
            repo = search_root / "repo"
            repo.mkdir()
            run("git", "init", "--quiet", cwd=repo)
            run("git", "config", "user.name", "Test User", cwd=repo)
            run("git", "config", "user.email", "test@example.com", cwd=repo)

            committed = repo / "CLAUDE.md"
            committed.write_text("committed\n", encoding="utf-8")
            (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
            run("git", "add", "CLAUDE.md", ".gitignore", cwd=repo)
            run("git", "commit", "--quiet", "-m", "initial", cwd=repo)

            untracked = repo / "untracked" / "CLAUDE.md"
            untracked.parent.mkdir()
            untracked.write_text("untracked\n", encoding="utf-8")

            ignored = repo / "node_modules" / "dependency" / "CLAUDE.md"
            ignored.parent.mkdir(parents=True)
            ignored.write_text("ignored\n", encoding="utf-8")

            staged = repo / "staged" / "CLAUDE.md"
            staged.parent.mkdir()
            staged.write_text("staged\n", encoding="utf-8")
            run("git", "add", "staged/CLAUDE.md", cwd=repo)

            result = run(
                str(SCRIPT),
                "--auto-commit",
                "--dry-run",
                str(search_root),
                cwd=search_root,
            )

            self.assertIn(f"would migrate: {committed}", result.stdout)
            self.assertIn(f"would commit: {repo}", result.stdout)
            self.assertNotIn(str(untracked), result.stdout + result.stderr)
            self.assertNotIn(str(ignored), result.stdout + result.stderr)
            self.assertNotIn(str(staged), result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
