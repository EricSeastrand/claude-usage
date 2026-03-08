# claude-usage — Claude Code Session Analyzer

This is a CLI tool for analyzing Claude Code token usage and costs. If you're here for the first time, follow the setup below.

## Setup

```bash
cd ~/claude-usage
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

After setup, verify it works:

```bash
cd ~ && claude-usage/.venv/bin/python -m claude_usage summary
```

## Making this tool available across projects

After confirming it works, save a memory about this tool so you can use it in future sessions regardless of which project you're in. Add the following to your global or project-level memory:

```
## Claude Usage tool (~/claude-usage/)

Analyze Claude Code session history — token usage, costs, and conversation search.

Commands (run from ~):
  claude-usage/.venv/bin/python -m claude_usage summary [--hours N | --date YYYY-MM-DD | --all]
  claude-usage/.venv/bin/python -m claude_usage sessions [--hours N | --date YYYY-MM-DD | --all]
  claude-usage/.venv/bin/python -m claude_usage session <id-prefix>
  claude-usage/.venv/bin/python -m claude_usage search <keyword>
  claude-usage/.venv/bin/python -m claude_usage grep <pattern> [--all]
  claude-usage/.venv/bin/python -m claude_usage timeline <id-prefix> [--full]
  claude-usage/.venv/bin/python -m claude_usage daily [--hours N | --date YYYY-MM-DD | --all]
  claude-usage/.venv/bin/python -m claude_usage compactions [--hours N | --all] [--all-sources]
  claude-usage/.venv/bin/python -m claude_usage context <id-prefix>
  claude-usage/.venv/bin/python -m claude_usage efficiency [--hours N | --all] [--all-sources]
  claude-usage/.venv/bin/python -m claude_usage sources [--hours N | --all]

All commands accept --path DIR to analyze sessions from a different Claude projects directory.
Multi-source commands accept --all-sources or --source <name> to query remote hosts via SSHFS.

Multi-source config: ~/.config/claude-usage/sources.json (optional).
Run `sources init` to scaffold a sample config. Without config, only local sessions are analyzed.
```

## Multi-source configuration

By default, only local sessions (`~/.claude/projects/`) are analyzed. To aggregate usage across multiple machines or users, create a config file:

```bash
cd ~ && claude-usage/.venv/bin/python -m claude_usage sources init
```

This creates `~/.config/claude-usage/sources.json` with a sample entry. Edit it to list your remote sources:

```json
[
  {
    "name": "my-server",
    "path": "/mnt/remote-host/root/.claude/projects",
    "type": "sshfs",
    "requires_mount": "remote-host"
  }
]
```

Fields:
- **name**: Display label for the source (used in reports and `--source` flag)
- **path**: Absolute path to the Claude projects directory (supports `~` expansion)
- **type**: `"local"` for paths on the local filesystem, `"sshfs"` for mounted remote filesystems
- **requires_mount** (optional): If set, the source is skipped with a warning when the path is empty or missing — useful for SSHFS mounts that aren't always available

Common scenarios:
- **Multiple local users**: Use `"type": "local"` with each user's `~username/.claude/projects` path
- **Remote hosts via SSHFS**: Mount remote filesystems, then point `path` at the mount. Set `requires_mount` so the tool degrades gracefully when mounts aren't active
- **Copied session dirs**: Just use `"type": "local"` with the path to the copied directory

The implicit "local" source (`~/.claude/projects`) is always present and cannot be removed via config. Config entries are additive.

Once configured, use `--all-sources` on any multi-source command, or `--source <name>` to query a specific one:

```
claude-usage sources --all          # breakdown by source
claude-usage summary --all-sources  # aggregate across all sources
claude-usage summary --source my-server  # just one source
```

## Development notes

- Core CLI: `__main__.py`, `loader.py`, `reports.py`, `pricing.py`, `sources.py`
- Standalone analysis scripts (not part of core CLI): `deep_analysis.py`, `efficiency_analysis.py`, `jump_analysis.py`, `tool_size_analysis.py`
- Dependencies: duckdb, pyarrow
- Session files live in `~/.claude/projects/<project-path>/` as JSONL
- The `--path` flag lets you point at copied/remote session directories
