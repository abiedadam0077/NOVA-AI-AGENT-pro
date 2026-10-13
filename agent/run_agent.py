#!/usr/bin/env python3
"""CLI entry point for the NOVA agent.

Used by the `nova-agent.yml` GitHub workflow and by local mock runs:

    python agent/run_agent.py --command "..." --workspace . \
        --result-json result.json --diff-patch diff.patch

Local testing without an OpenRouter key:

    python agent/run_agent.py --command "..." --workspace /tmp/ws \
        --mock-script script.json
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nova_agent.config import AgentConfig  # noqa: E402
from nova_agent.gitops import GitHelper  # noqa: E402
from nova_agent.mock import MockProvider  # noqa: E402
from nova_agent.openrouter import OpenRouterClient  # noqa: E402
from nova_agent.report import append_step_summary, to_step_summary  # noqa: E402
from nova_agent.runner import AgentRunner, RunResult  # noqa: E402
from nova_agent.tools import ToolExecutor  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the NOVA AI agent on a workspace.")
    parser.add_argument("--command", required=True, help="Task for the agent")
    parser.add_argument("--workspace", default=".", help="Directory the agent works in")
    parser.add_argument("--model", default=None, help="OpenRouter model id (overrides NOVA_MODEL)")
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument("--result-json", default="result.json", help="Where to write the structured result")
    parser.add_argument("--diff-patch", default="diff.patch", help="Where to write the review diff")
    parser.add_argument(
        "--mock-script",
        default=None,
        help="JSON file with scripted assistant messages (local testing without an API key)",
    )
    args = parser.parse_args(argv)

    workspace = os.path.abspath(args.workspace)
    config = AgentConfig.from_env(workspace=workspace)
    if args.model:
        config.model = args.model
    if args.max_iterations:
        config.max_iterations = args.max_iterations

    if args.mock_script:
        with open(args.mock_script, "r", encoding="utf-8") as handle:
            provider = MockProvider(json.load(handle))
    else:
        provider = OpenRouterClient(
            api_key=config.api_key, model=config.model, base_url=config.base_url
        )

    executor = ToolExecutor(
        workspace,
        command_timeout=config.command_timeout,
        max_output_chars=config.max_output_chars,
    )
    git = GitHelper(workspace)
    runner = AgentRunner(provider, executor, config, git=git)

    try:
        result = runner.run(args.command)
    except Exception as exc:  # noqa: BLE001 - always produce a result file
        result = RunResult(
            status="error",
            summary="",
            iterations=0,
            changed_files=[],
            tool_log=[],
            error=f"agent crashed: {type(exc).__name__}: {exc}",
            model=config.model,
        )

    result.model = config.model

    with open(args.result_json, "w", encoding="utf-8") as handle:
        json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2)

    git.make_patch(args.diff_patch)

    markdown = to_step_summary(result)
    append_step_summary(markdown)
    print(markdown)
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
