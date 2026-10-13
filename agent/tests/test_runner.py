import json

import pytest

from nova_agent.config import AgentConfig
from nova_agent.gitops import GitHelper
from nova_agent.mock import MockProvider, text_tool_message, tool_call_message
from nova_agent.runner import AgentRunner
from nova_agent.tools import ToolExecutor


def make_runner(workspace, script, **config_kwargs):
    config = AgentConfig(workspace=str(workspace), **config_kwargs)
    executor = ToolExecutor(str(workspace), command_timeout=10)
    git = GitHelper(str(workspace))
    provider = MockProvider(script)
    return AgentRunner(provider, executor, config, git=git), provider


def test_happy_path_write_verify_finish(git_workspace):
    script = [
        tool_call_message(
            "write_file",
            {"path": "hello.py", "content": "print('hello NOVA')\n"},
        ),
        tool_call_message("run_command", {"command": "python3 hello.py"}),
        tool_call_message(
            "finish", {"summary": "Created hello.py; verified it prints hello NOVA."}
        ),
    ]
    runner, _ = make_runner(git_workspace, script)
    result = runner.run("Create hello.py that prints hello NOVA")

    assert result.status == "success"
    assert result.iterations == 3
    assert (git_workspace / "hello.py").exists()
    assert "hello.py" in result.changed_files
    run_entries = [e for e in result.tool_log if e["tool"] == "run_command"]
    assert run_entries and run_entries[0]["ok"]
    assert "hello NOVA" in run_entries[0]["output"]
    assert "verified" in result.summary
    assert result.error is None


def test_failed_command_is_fed_back(git_workspace):
    script = [
        tool_call_message("run_command", {"command": "python3 missing.py"}),
        tool_call_message("finish", {"summary": "blocked"}),
    ]
    runner, provider = make_runner(git_workspace, script)
    result = runner.run("run it")

    assert result.tool_log[0]["ok"] is False
    # Second chat call must have seen the failing output as a tool message.
    second_call = provider.seen_messages[1]
    tool_messages = [m for m in second_call if m.get("role") == "tool"]
    assert tool_messages
    assert "FAILED" in tool_messages[-1]["content"] or "ERROR" in tool_messages[-1]["content"]


def test_error_then_self_fix(git_workspace):
    script = [
        tool_call_message("write_file", {"path": "app.py", "content": "print('a'b)\n"}),
        tool_call_message("run_command", {"command": "python3 app.py"}),
        tool_call_message(
            "edit_file",
            {"path": "app.py", "old_text": "print('a'b)", "new_text": "print('ab')"},
        ),
        tool_call_message("run_command", {"command": "python3 app.py"}),
        tool_call_message("finish", {"summary": "fixed syntax error; output 'ab'"}),
    ]
    runner, _ = make_runner(git_workspace, script)
    result = runner.run("make app.py print ab")

    assert result.status == "success"
    oks = [e["ok"] for e in result.tool_log if e["tool"] == "run_command"]
    assert oks == [False, True]


def test_max_iterations_guard(git_workspace):
    script = [
        tool_call_message("run_command", {"command": "true"}) for _ in range(5)
    ]
    runner, _ = make_runner(git_workspace, script, max_iterations=5)
    result = runner.run("loop forever")
    assert result.status == "max_iterations"
    assert result.iterations == 5


def test_provider_error_recorded(git_workspace):
    class BoomProvider:
        def chat(self, messages, tools=None, temperature=0.2):
            raise RuntimeError("boom")

    config = AgentConfig(workspace=str(git_workspace))
    runner = AgentRunner(
        BoomProvider(), ToolExecutor(str(git_workspace)), config, git=None
    )
    result = runner.run("x")
    assert result.status == "error"
    assert "boom" in result.error


def test_text_fallback_protocol(git_workspace):
    script = [
        text_tool_message(
            {"tool": "write_file", "args": {"path": "a.txt", "content": "x"}}
        ),
        text_tool_message({"tool": "finish", "args": {"summary": "done via text"}}),
    ]
    runner, provider = make_runner(git_workspace, script)
    result = runner.run("write a.txt")

    assert result.status == "success"
    assert result.summary == "done via text"
    assert (git_workspace / "a.txt").read_text() == "x"
    # Fallback results are returned to the model as user messages.
    second_call = provider.seen_messages[1]
    user_results = [
        m
        for m in second_call
        if m.get("role") == "user" and "[tool_result" in str(m.get("content"))
    ]
    assert user_results


def test_plain_text_reply_finishes(git_workspace):
    script = [{"role": "assistant", "content": "All done — nothing to change."}]
    runner, _ = make_runner(git_workspace, script)
    result = runner.run("status?")
    assert result.status == "success"
    assert "All done" in result.summary
    assert result.changed_files == []


def test_result_serializable_and_patch(git_workspace):
    script = [
        tool_call_message("write_file", {"path": "x.txt", "content": "1"}),
        tool_call_message("finish", {"summary": "ok"}),
    ]
    runner, _ = make_runner(git_workspace, script)
    result = runner.run("create x.txt")

    payload = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "x.txt" in payload

    git = GitHelper(str(git_workspace))
    patch_path = str(git_workspace.parent / "diff.patch")
    written = git.make_patch(patch_path)
    assert written == patch_path
    patch = open(patch_path, encoding="utf-8").read()
    assert "x.txt" in patch
    assert "+1" in patch


def test_changed_files_without_git(tmp_path):
    script = [
        tool_call_message("write_file", {"path": "y.txt", "content": "v"}),
        tool_call_message("finish", {"summary": "ok"}),
    ]
    runner, _ = make_runner(tmp_path, script)
    result = runner.run("create y.txt")
    assert result.status == "success"
    assert result.changed_files == ["y.txt"]
