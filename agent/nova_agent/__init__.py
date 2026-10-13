"""NOVA Agent core — an OpenRouter-driven autonomous coding agent.

Runs inside GitHub Actions (or locally with --mock-script) and executes a
user command against a workspace using a safe set of tools:
list_files, read_file, write_file, edit_file, run_command, finish.
"""

__version__ = "0.1.0"
