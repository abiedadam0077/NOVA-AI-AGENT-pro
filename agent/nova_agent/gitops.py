"""Git helpers: change detection and patch generation for review artifacts.

Tolerates non-git workspaces (local mock runs) by degrading to no-ops.
"""

import os
import subprocess
from typing import List, Optional


class GitHelper:
    def __init__(self, workspace: str):
        self.workspace = os.path.realpath(workspace)
        self.available = self._is_repo()

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", self.workspace, *args],
            capture_output=True,
            text=True,
        )

    def _is_repo(self) -> bool:
        try:
            result = self._run("rev-parse", "--is-inside-work-tree")
        except FileNotFoundError:
            return False
        return result.returncode == 0 and result.stdout.strip() == "true"

    def changed_files(self) -> List[str]:
        """Tracked modifications + untracked files (gitignored files excluded)."""
        if not self.available:
            return []
        result = self._run("status", "--porcelain")
        files: List[str] = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip()
            if " -> " in path:  # rename: "old -> new"
                path = path.split(" -> ", 1)[1]
            path = path.strip('"')
            if path:
                files.append(path)
        return sorted(set(files))

    def make_patch(self, patch_path: str) -> Optional[str]:
        """Write a reviewable unified diff (including untracked files).

        Uses `git add -A -N` (intent-to-add) so new files appear in the diff,
        then resets the index so staging state is preserved for the caller.
        Returns the patch path, or None when there is nothing to show.
        """
        if not self.available:
            return None
        self._run("add", "-A", "-N")
        diff = self._run("diff", "--no-color").stdout
        self._run("reset", "-q")  # harmless when the repo has no HEAD yet
        if not diff.strip():
            return None
        parent = os.path.dirname(os.path.abspath(patch_path))
        os.makedirs(parent or ".", exist_ok=True)
        with open(patch_path, "w", encoding="utf-8") as handle:
            handle.write(diff)
        return patch_path
