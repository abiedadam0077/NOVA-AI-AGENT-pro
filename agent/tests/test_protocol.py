import json

from nova_agent.protocol import TOOL_SCHEMAS, ToolCall, extract_tool_calls


def _native(name, args, call_id="call_1", content=""):
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def test_native_tool_call_parsed():
    calls, text = extract_tool_calls(
        _native("write_file", {"path": "a.txt", "content": "hi"}, content="doing it")
    )
    assert len(calls) == 1
    assert calls[0].name == "write_file"
    assert calls[0].arguments == {"path": "a.txt", "content": "hi"}
    assert calls[0].id == "call_1"
    assert not calls[0].is_text_fallback
    assert text == "doing it"


def test_native_bad_arguments_kept_raw():
    message = _native("write_file", {})
    message["tool_calls"][0]["function"]["arguments"] = "{not json"
    calls, _ = extract_tool_calls(message)
    assert calls[0].arguments == {"_unparsed": "{not json"}


def test_fenced_json_fallback():
    message = {
        "role": "assistant",
        "content": 'Sure!\n```json\n{"tool": "run_command", "args": {"command": "ls"}}\n```\nDone.',
    }
    calls, text = extract_tool_calls(message)
    assert len(calls) == 1
    assert calls[0].name == "run_command"
    assert calls[0].arguments == {"command": "ls"}
    assert calls[0].is_text_fallback
    assert "Sure!" in text


def test_bare_json_fallback():
    message = {
        "role": "assistant",
        "content": 'prefix {"tool": "finish", "args": {"summary": "all done"}} suffix',
    }
    calls, _ = extract_tool_calls(message)
    assert len(calls) == 1
    assert calls[0].name == "finish"
    assert calls[0].arguments == {"summary": "all done"}


def test_fallback_arguments_alias():
    message = {
        "role": "assistant",
        "content": '```json\n{"tool": "read_file", "arguments": {"path": "x"}}\n```',
    }
    calls, _ = extract_tool_calls(message)
    assert calls[0].arguments == {"path": "x"}


def test_plain_text_has_no_calls():
    calls, text = extract_tool_calls(
        {"role": "assistant", "content": "I finished the task successfully."}
    )
    assert calls == []
    assert text == "I finished the task successfully."


def test_native_wins_over_text():
    message = _native(
        "finish",
        {"summary": "s"},
        content='```json\n{"tool": "run_command", "args": {"command": "rm x"}}\n```',
    )
    calls, _ = extract_tool_calls(message)
    assert len(calls) == 1
    assert calls[0].name == "finish"
    assert not calls[0].is_text_fallback


def test_missing_call_id_gets_default():
    message = _native("finish", {"summary": "s"})
    del message["tool_calls"][0]["id"]
    calls, _ = extract_tool_calls(message)
    assert calls[0].id == "call_0"


def test_tool_schemas_shape():
    names = set()
    for schema in TOOL_SCHEMAS:
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] and fn["description"]
        assert fn["parameters"]["type"] == "object"
        names.add(fn["name"])
    assert names == {
        "list_files",
        "read_file",
        "write_file",
        "edit_file",
        "run_command",
        "finish",
    }
