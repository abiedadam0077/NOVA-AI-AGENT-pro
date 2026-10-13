import os

import pytest

from nova_agent.tools import ToolExecutor, ToolError


@pytest.fixture
def executor(tmp_path):
    return ToolExecutor(str(tmp_path), command_timeout=10, max_output_chars=4000)


# ------------------------------------------------------------- file tools


def test_write_then_read_roundtrip(executor, tmp_path):
    res = executor.execute("write_file", {"path": "a/b.txt", "content": "hello"})
    assert res["ok"], res["output"]
    assert (tmp_path / "a" / "b.txt").read_text() == "hello"
    res = executor.execute("read_file", {"path": "a/b.txt"})
    assert res["ok"]
    assert "1: hello" in res["output"]


def test_read_offset_and_limit(executor):
    executor.execute("write_file", {"path": "f.txt", "content": "l1\nl2\nl3\nl4\n"})
    res = executor.execute("read_file", {"path": "f.txt", "offset": 2, "limit": 2})
    assert res["ok"]
    assert "2: l2" in res["output"]
    assert "3: l3" in res["output"]
    assert "l1" not in res["output"]
    assert "l4" not in res["output"]


def test_read_missing_file(executor):
    res = executor.execute("read_file", {"path": "nope.txt"})
    assert not res["ok"]
    assert "not found" in res["output"]


def test_edit_replaces_first_occurrence(executor, tmp_path):
    executor.execute("write_file", {"path": "f.txt", "content": "x = 1\nx = 1\n"})
    res = executor.execute(
        "edit_file", {"path": "f.txt", "old_text": "x = 1", "new_text": "x = 2"}
    )
    assert res["ok"]
    assert "2 occurrences" in res["output"]
    assert (tmp_path / "f.txt").read_text() == "x = 2\nx = 1\n"


def test_edit_missing_old_text(executor):
    executor.execute("write_file", {"path": "f.txt", "content": "abc"})
    res = executor.execute(
        "edit_file", {"path": "f.txt", "old_text": "zzz", "new_text": "y"}
    )
    assert not res["ok"]
    assert "not found" in res["output"]


def test_path_escape_relative_blocked(executor):
    res = executor.execute("read_file", {"path": "../../../etc/passwd"})
    assert not res["ok"]
    assert "escapes the workspace" in res["output"]


def test_path_escape_absolute_blocked(executor):
    res = executor.execute("write_file", {"path": "/tmp/nova-evil.txt", "content": "x"})
    assert not res["ok"]
    assert "escapes the workspace" in res["output"]
    assert not os.path.exists("/tmp/nova-evil.txt")


def test_list_files_excludes_noise_dirs(executor, tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("x")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print(1)")
    res = executor.execute("list_files", {})
    assert res["ok"]
    assert "src/main.py" in res["output"]
    assert ".git" not in res["output"]
    assert "node_modules" not in res["output"]


def test_unknown_tool(executor):
    res = executor.execute("launch_missiles", {})
    assert not res["ok"]
    assert "unknown tool" in res["output"]


# --------------------------------------------------------- command safety


def test_run_command_success(executor, tmp_path):
    res = executor.execute("run_command", {"command": "echo hello-nova"})
    assert res["ok"]
    assert "hello-nova" in res["output"]
    assert "exit code 0" in res["output"]


def test_run_command_runs_in_workspace(executor, tmp_path):
    res = executor.execute("run_command", {"command": "pwd"})
    assert res["ok"]
    assert os.path.realpath(str(tmp_path)) in res["output"]


def test_run_command_cwd_subdir(executor, tmp_path):
    (tmp_path / "sub").mkdir()
    res = executor.execute("run_command", {"command": "pwd", "cwd": "sub"})
    assert res["ok"]
    assert res["output"].rstrip().endswith("sub")


def test_run_command_failure_reports_exit_code(executor):
    res = executor.execute("run_command", {"command": "exit 3"})
    assert not res["ok"]
    assert "exit code 3" in res["output"]


def test_run_command_timeout(executor):
    res = executor.execute("run_command", {"command": "sleep 30", "timeout": 1})
    assert not res["ok"]
    assert "timed out" in res["output"]


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -rf /*",
        "rm -fr /etc",
        "sudo rm -rf .",
        "mkfs.ext4 /dev/sda",
        "dd if=/dev/zero of=/dev/sda",
        "git push --force origin main",
    ],
)
def test_run_command_denies_destructive(executor, command):
    res = executor.execute("run_command", {"command": command})
    assert not res["ok"]
    assert "BLOCKED" in res["output"]


def test_run_command_allows_relative_cleanup(executor, tmp_path):
    (tmp_path / "junk").mkdir()
    (tmp_path / "junk" / "f").write_text("x")
    res = executor.execute("run_command", {"command": "rm -rf junk/"})
    assert res["ok"], res["output"]
    assert not (tmp_path / "junk").exists()


def test_run_command_env_scrubbed(executor, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-SUPER-SECRET")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_SECRET")
    res = executor.execute(
        "run_command",
        {
            "command": "python3 -c \"import os; print(os.environ.get('OPENROUTER_API_KEY','') + os.environ.get('GITHUB_TOKEN',''))\""
        },
    )
    assert res["ok"]
    assert "SECRET" not in res["output"]
    # Normal environment (PATH) is preserved.
    res = executor.execute("run_command", {"command": "echo $PATH | head -c 20"})
    assert res["ok"]
    assert len(res["output"].splitlines()[-1].strip()) > 0


def test_run_command_output_truncated(tmp_path):
    executor = ToolExecutor(str(tmp_path), command_timeout=10, max_output_chars=1000)
    res = executor.execute(
        "run_command", {"command": "python3 -c \"print('A'*100000)\""}
    )
    assert res["ok"]
    assert "truncated" in res["output"]
    assert len(res["output"]) < 2000


def test_workspace_must_exist(tmp_path):
    with pytest.raises(ToolError):
        ToolExecutor(str(tmp_path / "missing"))
