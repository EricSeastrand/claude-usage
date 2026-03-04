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
) -> list[Path]:
    """Find all session JSONL files under a Claude projects directory.

    Args:
        hours: Only include files modified within this many hours.
        date: Only include files modified on this date (YYYY-MM-DD).
              If both hours and date are None, returns all files.
        path: Custom path to projects directory (default: ~/.claude/projects).
    """
    base = Path(path) if path else CLAUDE_DIR
    if not base.exists():
        return []

    all_files = sorted(base.glob("*/*.jsonl"))

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


def _extract_records(files: list[Path]) -> list[dict]:
    """Extract usage records and session metadata from JSONL files."""
    records = []

    for path in files:
        session_id = path.stem
        project = path.parent.name
        first_prompt = None

        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rec_type = rec.get("type")

                # Capture first real user message as session prompt
                if rec_type == "user" and first_prompt is None:
                    content = rec.get("message", {}).get("content", "")
                    if isinstance(content, str) and content and not content.startswith("<"):
                        first_prompt = content[:200]
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text = part["text"]
                                if text and not text.startswith("<"):
                                    first_prompt = text[:200]
                                    break

                if rec_type != "assistant":
                    continue

                msg = rec.get("message", {})
                usage = msg.get("usage")
                if not usage:
                    continue

                # Skip synthetic/zero-token records
                total = (
                    usage.get("input_tokens", 0)
                    + usage.get("output_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0)
                )
                if total == 0:
                    continue

                cache = usage.get("cache_creation", {})

                records.append({
                    "session_id": session_id,
                    "project": project,
                    "timestamp": rec.get("timestamp", ""),
                    "model": msg.get("model", "unknown"),
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_write_5m_tokens": cache.get("ephemeral_5m_input_tokens", 0),
                    "cache_write_1h_tokens": cache.get("ephemeral_1h_input_tokens", 0),
                    "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
                    "first_prompt": first_prompt or "(no prompt)",
                })

    return records


def load_usage_records(
    files: list[Path],
) -> duckdb.DuckDBPyRelation | None:
    """Load usage records from JSONL files into a DuckDB relation.

    Returns None if no usage records found.
    """
    records = _extract_records(files)
    if not records:
        return None

    # Build a PyArrow table — DuckDB 1.4 can't scan plain list-of-dicts
    table = pa.table({
        "session_id": [r["session_id"] for r in records],
        "project": [r["project"] for r in records],
        "timestamp": [r["timestamp"] for r in records],
        "model": [r["model"] for r in records],
        "input_tokens": pa.array([r["input_tokens"] for r in records], type=pa.int64()),
        "output_tokens": pa.array([r["output_tokens"] for r in records], type=pa.int64()),
        "cache_write_5m_tokens": pa.array([r["cache_write_5m_tokens"] for r in records], type=pa.int64()),
        "cache_write_1h_tokens": pa.array([r["cache_write_1h_tokens"] for r in records], type=pa.int64()),
        "cache_read_tokens": pa.array([r["cache_read_tokens"] for r in records], type=pa.int64()),
        "first_prompt": [r["first_prompt"] for r in records],
    })

    conn = duckdb.connect()
    conn.register("_raw", table)
    conn.execute("""
        CREATE TABLE usage AS
        SELECT
            session_id, project, timestamp::TIMESTAMP AS timestamp,
            model, input_tokens, output_tokens,
            cache_write_5m_tokens, cache_write_1h_tokens, cache_read_tokens,
            first_prompt
        FROM _raw
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


def find_session_file(session_prefix: str, path: str | None = None) -> Path | None:
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
