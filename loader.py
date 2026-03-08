"""Discover and load Claude Code session JSONL files into DuckDB."""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

import duckdb
import pyarrow as pa


CLAUDE_DIR = Path.home() / ".claude" / "projects"


def discover_session_files(
    hours: int | None = None,
    date: str | None = None,
    path: str | None = None,
    include_subagents: bool = False,
) -> list[Path]:
    """Find all session JSONL files under a Claude projects directory.

    Args:
        hours: Only include files modified within this many hours.
        date: Only include files modified on this date (YYYY-MM-DD).
              If both hours and date are None, returns all files.
        path: Custom path to projects directory (default: ~/.claude/projects).
        include_subagents: Also discover subagent files under */subagents/.
    """
    base = Path(path) if path else CLAUDE_DIR
    if not base.exists():
        return []

    all_files = sorted(base.glob("*/*.jsonl"))
    if include_subagents:
        all_files.extend(sorted(base.glob("*/*/subagents/agent-*.jsonl")))

    if date:
        target = datetime.strptime(date, "%Y-%m-%d").date()
        return [
            f for f in all_files
            if datetime.fromtimestamp(f.stat().st_mtime).date() == target
        ]

    if hours is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        return [
            f for f in all_files
            if datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc) >= cutoff
        ]

    return all_files


def _is_subagent_file(path: Path) -> tuple[bool, str | None]:
    """Check if a path is a subagent JSONL file. Returns (is_subagent, agent_id)."""
    if path.parent.name == "subagents" and path.stem.startswith("agent-"):
        return True, path.stem.removeprefix("agent-")
    return False, None


def _extract_first_prompt(rec: dict) -> str | None:
    """Extract first real user message text from a record."""
    content = rec.get("message", {}).get("content", "")
    if isinstance(content, str) and content and not content.startswith("<"):
        return content[:200]
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part["text"]
                if text and not text.startswith("<"):
                    return text[:200]
    return None


def _extract_records(
    files: list[Path],
    source: str = "local",
) -> tuple[list[dict], list[dict]]:
    """Extract usage records, compactions, and session metadata from JSONL files.

    Returns (usage_records, compaction_records).
    """
    records = []
    compactions = []

    for path in files:
        is_subagent, agent_id = _is_subagent_file(path)

        if is_subagent:
            # Subagent: session_id is the parent's session dir name
            session_id = path.parent.parent.name
            # Project is two levels up from the subagent file
            project = path.parent.parent.parent.name
        else:
            session_id = path.stem
            project = path.parent.name

        first_prompt = None
        turn_number = 0

        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rec_type = rec.get("type")

                # Capture first real user message as session prompt
                if rec_type == "user" and first_prompt is None:
                    first_prompt = _extract_first_prompt(rec)

                # Extract compaction events
                if (rec_type == "system"
                        and rec.get("subtype") == "compact_boundary"):
                    meta = rec.get("compactMetadata", {})
                    compactions.append({
                        "session_id": session_id,
                        "project": project,
                        "source": source,
                        "timestamp": rec.get("timestamp", ""),
                        "trigger": meta.get("trigger", "unknown"),
                        "pre_tokens": meta.get("preTokens", 0),
                        "turn_number": turn_number,
                    })
                    continue

                if rec_type != "assistant":
                    continue

                msg = rec.get("message", {})
                usage = msg.get("usage")
                if not usage:
                    continue

                # Skip synthetic/zero-token records
                input_tok = usage.get("input_tokens", 0)
                output_tok = usage.get("output_tokens", 0)
                cache_create = usage.get("cache_creation_input_tokens", 0)
                cache_read = usage.get("cache_read_input_tokens", 0)

                total = input_tok + output_tok + cache_create + cache_read
                if total == 0:
                    continue

                turn_number += 1
                cache = usage.get("cache_creation", {})
                cw5m = cache.get("ephemeral_5m_input_tokens", 0)
                cw1h = cache.get("ephemeral_1h_input_tokens", 0)

                # Effective context = everything sent to the API on this turn
                effective_context = input_tok + cache_read + cache_create

                records.append({
                    "session_id": session_id,
                    "project": project,
                    "source": source,
                    "timestamp": rec.get("timestamp", ""),
                    "model": msg.get("model", "unknown"),
                    "input_tokens": input_tok,
                    "output_tokens": output_tok,
                    "cache_write_5m_tokens": cw5m,
                    "cache_write_1h_tokens": cw1h,
                    "cache_read_tokens": cache_read,
                    "first_prompt": first_prompt or "(no prompt)",
                    "is_subagent": is_subagent,
                    "agent_id": agent_id or "",
                    "turn_number": turn_number,
                    "effective_context": effective_context,
                })

    return records, compactions


def load_usage_records(
    files: list[Path],
) -> duckdb.DuckDBPyConnection | None:
    """Load usage records from JSONL files into a DuckDB connection.

    Creates tables:
        - usage: per-turn token usage with context tracking
        - compactions: compaction boundary events

    Returns None if no usage records found.
    """
    records, compactions = _extract_records(files)
    if not records:
        return None

    return _build_db(records, compactions)


def load_multi_source(
    source_files: list[tuple[str, Path]],
) -> duckdb.DuckDBPyConnection | None:
    """Load usage records from multiple sources into a single DuckDB connection.

    Args:
        source_files: list of (source_name, file_path) tuples.
    """
    all_records = []
    all_compactions = []

    # Group files by source for efficient extraction
    by_source: dict[str, list[Path]] = {}
    for source_name, file_path in source_files:
        by_source.setdefault(source_name, []).append(file_path)

    for source_name, files in by_source.items():
        records, compactions = _extract_records(files, source=source_name)
        all_records.extend(records)
        all_compactions.extend(compactions)

    if not all_records:
        return None
    return _build_db(all_records, all_compactions)


def _build_db(
    records: list[dict],
    compactions: list[dict],
) -> duckdb.DuckDBPyConnection:
    """Build a DuckDB connection from extracted records and compactions."""
    # Build a PyArrow table — DuckDB 1.4 can't scan plain list-of-dicts
    table = pa.table({
        "session_id": [r["session_id"] for r in records],
        "project": [r["project"] for r in records],
        "source": [r["source"] for r in records],
        "timestamp": [r["timestamp"] for r in records],
        "model": [r["model"] for r in records],
        "input_tokens": pa.array([r["input_tokens"] for r in records], type=pa.int64()),
        "output_tokens": pa.array([r["output_tokens"] for r in records], type=pa.int64()),
        "cache_write_5m_tokens": pa.array([r["cache_write_5m_tokens"] for r in records], type=pa.int64()),
        "cache_write_1h_tokens": pa.array([r["cache_write_1h_tokens"] for r in records], type=pa.int64()),
        "cache_read_tokens": pa.array([r["cache_read_tokens"] for r in records], type=pa.int64()),
        "first_prompt": [r["first_prompt"] for r in records],
        "is_subagent": [r["is_subagent"] for r in records],
        "agent_id": [r["agent_id"] for r in records],
        "turn_number": pa.array([r["turn_number"] for r in records], type=pa.int32()),
        "effective_context": pa.array([r["effective_context"] for r in records], type=pa.int64()),
    })

    conn = duckdb.connect()
    conn.register("_raw", table)
    conn.execute("""
        CREATE TABLE usage AS
        SELECT
            session_id, project, source, timestamp::TIMESTAMP AS timestamp,
            model, input_tokens, output_tokens,
            cache_write_5m_tokens, cache_write_1h_tokens, cache_read_tokens,
            first_prompt, is_subagent, agent_id, turn_number, effective_context
        FROM _raw
    """)

    # Create compactions table
    if compactions:
        comp_table = pa.table({
            "session_id": [c["session_id"] for c in compactions],
            "project": [c["project"] for c in compactions],
            "source": [c["source"] for c in compactions],
            "timestamp": [c["timestamp"] for c in compactions],
            "trigger": [c["trigger"] for c in compactions],
            "pre_tokens": pa.array([c["pre_tokens"] for c in compactions], type=pa.int64()),
            "turn_number": pa.array([c["turn_number"] for c in compactions], type=pa.int32()),
        })
        conn.register("_comp_raw", comp_table)
        conn.execute("""
            CREATE TABLE compactions AS
            SELECT
                session_id, project, source, timestamp::TIMESTAMP AS timestamp,
                trigger, pre_tokens, turn_number
            FROM _comp_raw
        """)
    else:
        conn.execute("""
            CREATE TABLE compactions (
                session_id VARCHAR, project VARCHAR, source VARCHAR,
                timestamp TIMESTAMP, trigger VARCHAR,
                pre_tokens BIGINT, turn_number INTEGER
            )
        """)

    return conn


# ---------------------------------------------------------------------------
# Message-level loading (for timeline & grep)
# ---------------------------------------------------------------------------


def _extract_text(content) -> str:
    """Extract plain text from a message content field (string or block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "thinking":
                    pass  # skip thinking blocks
        return "\n".join(parts)
    return ""


def _extract_tools(content) -> list[str]:
    """Extract tool_use summaries from assistant content blocks."""
    tools = []
    if not isinstance(content, list):
        return tools
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            name = block.get("name", "?")
            inp = block.get("input", {})
            # Build a brief arg summary
            brief = ""
            for key in ("file_path", "pattern", "command", "query", "path", "url", "skill"):
                if key in inp:
                    val = str(inp[key])
                    if len(val) > 80:
                        val = val[:77] + "..."
                    brief = f"{key}={val}"
                    break
            tools.append(f"{name}({brief})")
    return tools


def find_session_file(
    session_prefix: str,
    path: str | None = None,
    include_subagents: bool = False,
) -> Path | None:
    """Find a single session JSONL file by ID prefix.

    Returns the Path, or None. Prints an error if ambiguous.
    """
    base = Path(path) if path else CLAUDE_DIR
    if not base.exists():
        return None

    matches = [
        f for f in base.glob("*/*.jsonl")
        if f.stem.startswith(session_prefix)
    ]
    if include_subagents:
        matches.extend(
            f for f in base.glob("*/*/subagents/agent-*.jsonl")
            if f.stem.removeprefix("agent-").startswith(session_prefix)
        )

    if not matches:
        print(f"\n  No session file found matching '{session_prefix}'")
        return None
    if len(matches) > 1:
        print(f"\n  Multiple sessions match '{session_prefix}':")
        for f in sorted(matches)[:10]:
            print(f"    {f.stem}")
        return None

    return matches[0]


def load_session_messages(session_file: Path) -> list[dict]:
    """Load all user/assistant messages from a session JSONL file.

    Returns list of dicts with keys:
        timestamp, role, text, tools (list of tool_use summaries)
    """
    messages = []

    with open(session_file) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            rec_type = rec.get("type")
            if rec_type not in ("user", "assistant"):
                continue

            ts = rec.get("timestamp", "")
            content = rec.get("message", {}).get("content", "")

            if rec_type == "user":
                text = _extract_text(content)
                # Skip system/tool_result-only messages
                if not text or text.startswith("<"):
                    continue
                messages.append({
                    "timestamp": ts,
                    "role": "user",
                    "text": text,
                    "tools": [],
                })
            else:
                text = _extract_text(content)
                tools = _extract_tools(content)
                # Skip assistant messages with no text and no tools
                if not text and not tools:
                    continue
                messages.append({
                    "timestamp": ts,
                    "role": "assistant",
                    "text": text,
                    "tools": tools,
                })

    return messages


def grep_messages(
    files: list[Path], pattern: str,
) -> Generator[dict, None, None]:
    """Search all user/assistant messages across sessions for a regex pattern.

    Yields dicts with keys:
        session_id, project, timestamp, role, text, match_start, match_end
    """
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        print(f"\n  Invalid regex pattern: {e}")
        return

    for path in files:
        session_id = path.stem
        project = path.parent.name

        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rec_type = rec.get("type")
                if rec_type not in ("user", "assistant"):
                    continue

                content = rec.get("message", {}).get("content", "")
                text = _extract_text(content)
                if not text:
                    continue

                m = regex.search(text)
                if m:
                    yield {
                        "session_id": session_id,
                        "project": project,
                        "timestamp": rec.get("timestamp", ""),
                        "role": rec_type,
                        "text": text,
                        "match_start": m.start(),
                        "match_end": m.end(),
                    }
