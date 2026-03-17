# claude-usage

CLI tool that analyzes token usage and estimates costs from Claude Code's local session files. Uses DuckDB for fast SQL queries over JSONL.

## What it does

Claude Code stores conversation data as JSONL files in `~/.claude/projects/`. This tool reads those files and gives you:

- **Cost estimates** broken down by model and cache tier
- **Session search** by keyword (user prompts) or regex (all messages)
- **Conversation timelines** showing the back-and-forth of any session
- **Daily/hourly usage breakdowns**
- **Context window tracking** — how full your 200K window gets per turn
- **Compaction analysis** — which sessions hit the context limit and got compacted
- **Efficiency metrics** — cost-per-turn, context ceiling stats
- **Multi-source aggregation** — combine usage from multiple machines via SSHFS

## Install

```bash
git clone https://github.com/EricSeastrand/claude-usage.git ~/claude-usage
cd ~/claude-usage
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Requires Python 3.11+.

**Important:** Python's `-m claude_usage` needs a `claude_usage` package on the path. The tool is designed to be run from `~` so that `claude-usage/` (the repo dir) is found. If you get `ModuleNotFoundError`, create a symlink:

```bash
ln -sf ~/claude-usage ~/claude_usage
```

## Usage

All commands are run from the repo's parent directory:

```bash
cd ~ && claude-usage/.venv/bin/python -m claude_usage <command> [options]
```

### Commands

**Core reports:**

- `summary` — Aggregate token usage and cost breakdown
- `sessions` — List sessions with per-session stats
- `session <id-prefix>` — Detailed view of one session (partial ID match)
- `daily` — Day-by-day usage breakdown

**Search:**

- `search <keyword>` — Find sessions by user prompt text
- `grep <pattern>` — Regex search across all messages (user + assistant)
- `timeline <id-prefix>` — Show conversation timeline (`--full` for message content)

**Context analysis:**

- `compactions` — Sessions that hit the context window limit and were compacted
- `context <id-prefix>` — Per-turn context growth curve for a specific session
- `efficiency` — Health metrics: cost-per-turn, context ceiling stats
- `segments` — Context quality analysis split by compaction boundaries

**Multi-source:**

- `sources` — Usage breakdown by source (local, remote hosts)
- `sources init` — Create a sample multi-source config file

### Time filters

Most commands accept these (mutually exclusive) filters:

- `--hours N` — Look back N hours (default: 24)
- `--date YYYY-MM-DD` — Specific date only
- `--all` — No time filter

### Remote sessions

Use `--path DIR` to point at a different Claude projects directory (e.g., copied from another machine):

```bash
claude-usage/.venv/bin/python -m claude_usage sessions --path /mnt/backup/.claude/projects
```

### Multi-source aggregation

To combine usage across multiple machines, create a config:

```bash
claude-usage/.venv/bin/python -m claude_usage sources init
```

This creates `~/.config/claude-usage/sources.json`. Edit it to add remote sources (SSHFS mounts, other local users, copied directories). See the CLAUDE.md in this repo for full config details.

Once configured:

```bash
# Breakdown by source
claude-usage/.venv/bin/python -m claude_usage sources --all

# Aggregate across all sources
claude-usage/.venv/bin/python -m claude_usage summary --all-sources

# Query one specific source
claude-usage/.venv/bin/python -m claude_usage summary --source my-server
```

The implicit "local" source (`~/.claude/projects`) is always present. Config entries are additive.

## Key concepts

- **effective_context** — The actual context window size for a given turn: `input_tokens + cache_read_tokens + cache_create_tokens`. This is what determines how "full" your 200K window is.
- **Compaction** — When the context window approaches ~167-170K tokens, Claude Code compresses the conversation history. The `compactions` command shows which sessions hit this wall. After compaction, context typically drops to ~46-59K.
- **Segments** — A segment is the stretch of conversation between compaction events (or from start to first compaction). The `segments` command analyzes each segment independently — peak context, turns, growth rate — so you can see whether your conversations are staying lean or repeatedly hitting the wall.

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

# Which sessions blew up the context window?
claude-usage/.venv/bin/python -m claude_usage compactions --all

# How is my context discipline trending?
claude-usage/.venv/bin/python -m claude_usage segments --all
```

## Where Claude stores sessions

Claude Code writes JSONL session files to:

```
~/.claude/projects/<project-dir-path>/
```

Each project directory name is the absolute path with slashes replaced by dashes, e.g.:

- `~/.claude/projects/-home-eric-mission-control/` for `~/mission-control`
- `~/.claude/projects/-home-eric-claude-usage/` for `~/claude-usage`

Each session is a `.jsonl` file named by UUID. Subagent sessions live under `<project>/subagents/agent-*.jsonl`.

## Analysis scripts

Standalone scripts for deeper one-off investigations (not part of the core CLI):

- **`deep_analysis.py`** — Context window size distribution, biggest sessions by peak/growth, biggest single-turn jumps, cost decomposition, session length distribution, average context per turn
- **`efficiency_analysis.py`** — How much of total spend is re-reading the same context? Ceiling/compression stats, trivial output turn analysis, per-project breakdown, "conversation tax" calculation
- **`jump_analysis.py`** — Drills into specific sessions to show turn-by-turn context trajectory and what caused the biggest jumps. Accepts session IDs as positional args or auto-detects the worst offenders
- **`tool_size_analysis.py`** — Tool result payload sizes: distribution, per-tool breakdown, biggest individual results, repeated noisy patterns (e.g., huge file reads)

```bash
cd ~ && claude-usage/.venv/bin/python claude-usage/deep_analysis.py
cd ~ && claude-usage/.venv/bin/python claude-usage/tool_size_analysis.py
cd ~ && claude-usage/.venv/bin/python claude-usage/jump_analysis.py [session_id ...]
```

All analysis scripts support `--path DIR` for remote directories.
