import os
import subprocess
import sys

import pytest

# Make `nova_agent` importable without installation.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def git_workspace(tmp_path):
    """A fresh git repository to act as the agent workspace."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for key, value in [
        ("user.email", "test@nova.local"),
        ("user.name", "NOVA Test"),
        ("commit.gpgsign", "false"),
    ]:
        subprocess.run(["git", "-C", str(tmp_path), "config", key, value], check=True)
    return tmp_path
