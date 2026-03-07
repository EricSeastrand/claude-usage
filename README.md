# claude-usage

CLI tool that analyzes token usage and estimates costs from Claude Code's local session files. Uses DuckDB for fast SQL queries over JSONL.

## What it does

Claude Code stores conversation data as JSONL files in `~/.claude/projects/`. This tool reads those files and gives you:

- **Cost estimates** broken down by model and cache tier
- **Session search** by keyword (user prompts) or regex (all messages)
- **Conversation timelines** showing the back-and-forth of any session
- **Daily/hourly usage breakdowns**

## Install

```bash
git clone <repo-url> ~/claude-usage
cd ~/claude-usage
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Requires Python 3.11+.

## Usage

All commands are run from the repo's parent directory:

```bash
cd ~ && claude-usage/.venv/bin/python -m claude_usage <command> [options]
```

### Commands

- `summary` -- Aggregate token usage and cost breakdown
- `sessions` -- List sessions with per-session stats
- `session <id-prefix>` -- Detailed view of one session (partial ID match)
- `search <keyword>` -- Find sessions by user prompt text
- `grep <pattern>` -- Regex search across all messages (user + assistant)
- `timeline <id-prefix>` -- Show conversation timeline (`--full` for message content)
- `daily` -- Day-by-day usage breakdown

### Time filters

Most commands accept these (mutually exclusive) filters:

- `--hours N` -- Look back N hours (default: 24)
- `--date YYYY-MM-DD` -- Specific date only
- `--all` -- No time filter

### Remote sessions

Use `--path DIR` to point at a different Claude projects directory (e.g., copied from another machine):

```bash
claude-usage/.venv/bin/python -m claude_usage sessions --path /mnt/backup/.claude/projects
```

## Examples

```bash
# Today's cost summary
claude-usage/.venv/bin/python -m claude_usage summary

# All sessions this week
claude-usage/.venv/bin/python -m claude_usage sessions --hours 168

# Find conversations about "wazuh"
claude-usage/.venv/bin/python -m claude_usage search wazuh

# Regex search across all messages, all time
claude-usage/.venv/bin/python -m claude_usage grep "docker.host.1" --all

# Full conversation replay
claude-usage/.venv/bin/python -m claude_usage timeline abc123 --full
```

## Where Claude stores sessions

Claude Code writes JSONL session files to:

```
~/.claude/projects/<project-dir-path>/
```

Each project directory name is the absolute path with slashes replaced by dashes, e.g.:

- `~/.claude/projects/-home-eric-mission-control/` for `~/mission-control`
- `~/.claude/projects/-home-eric-claude-usage/` for `~/claude-usage`

Each session is a `.jsonl` file named by UUID.

## Analysis scripts

The repo also includes standalone analysis scripts for deeper exploration. These are not part of the core CLI but can be useful for one-off investigations:

- `deep_analysis.py` -- Token usage pattern analysis
- `efficiency_analysis.py` -- Session efficiency metrics
- `jump_analysis.py` -- Context jump detection
- `tool_size_analysis.py` -- Tool result size analysis
