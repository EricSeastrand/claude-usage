"""Inference + tool-call latency analysis from Claude Code session JSONL files.

For each assistant turn this extracts:
    inference_seconds   — wall time between the preceding user message
                          (either a real prompt or a tool_result) and the
                          assistant's response being written.
    tool_batch_seconds  — wall time between the assistant firing tool_use(s)
                          and the next user message arriving with tool_results.
                          NULL if the assistant turn produced no tools.

These are wall-clock deltas from client-side JSONL timestamps — good enough to
spot trends and step-changes (e.g. a model switch doubling output tokens),
not precise enough for profiling a single turn.
"""

import json
from datetime import datetime
from pathlib import Path

import duckdb
import pyarrow as pa


# Drop obviously-bogus deltas (clock skew, overnight pauses).
_MAX_SECONDS = 3600.0


def _extract_tool_uses(content) -> list[str]:
    out: list[str] = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                out.append(b.get("name", "?"))
    return out


def _has_tool_result(content) -> bool:
    if not isinstance(content, list):
        return False
    return any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )


def _session_meta(path: Path) -> tuple[str, str, bool]:
    """Return (session_id, project, is_subagent) — mirrors loader.py conventions."""
    if path.parent.name == "subagents" and path.stem.startswith("agent-"):
        return path.parent.parent.name, path.parent.parent.parent.name, True
    return path.stem, path.parent.name, False


def _load_and_sort(path: Path) -> list[tuple[datetime, dict]]:
    """Load user/assistant records from a JSONL file, sorted by timestamp."""
    out: list[tuple[datetime, dict]] = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") not in ("user", "assistant"):
                    continue
                ts = rec.get("timestamp", "")
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    continue
                out.append((dt, rec))
    except OSError:
        return []
    out.sort(key=lambda x: x[0])
    return out


def _extract_records(files: list[Path], source: str = "local") -> list[dict]:
    records: list[dict] = []

    for path in files:
        session_id, project, is_subagent = _session_meta(path)
        seq = _load_and_sort(path)
        if not seq:
            continue

        prev_user_ts: datetime | None = None
        pending_tool_idx: int | None = None
        pending_tool_ts: datetime | None = None
        turn = 0

        for dt, rec in seq:
            rtype = rec.get("type")
            msg = rec.get("message", {}) if isinstance(rec.get("message"), dict) else {}
            content = msg.get("content", "")

            if rtype == "assistant":
                model = msg.get("model", "unknown") or "unknown"
                # Skip harness-synthesized assistant rows — they don't reflect real inference.
                if model.startswith("<") or model == "unknown":
                    prev_user_ts = None
                    pending_tool_idx = None
                    continue

                tools = _extract_tool_uses(content)
                usage = msg.get("usage") or {}

                inference_s: float | None = None
                if prev_user_ts is not None:
                    delta = (dt - prev_user_ts).total_seconds()
                    if 0 < delta < _MAX_SECONDS:
                        inference_s = delta

                turn += 1
                records.append({
                    "session_id": session_id,
                    "project": project,
                    "source": source,
                    "timestamp": dt.isoformat(),
                    "model": model,
                    "inference_seconds": inference_s,
                    "tool_batch_seconds": None,
                    "tool_names": tools,
                    "tool_count": len(tools),
                    "input_tokens": int(usage.get("input_tokens", 0) or 0),
                    "output_tokens": int(usage.get("output_tokens", 0) or 0),
                    "cache_read_tokens": int(usage.get("cache_read_input_tokens", 0) or 0),
                    "cache_create_tokens": int(usage.get("cache_creation_input_tokens", 0) or 0),
                    "turn_number": turn,
                    "is_subagent": is_subagent,
                })

                prev_user_ts = None
                if tools:
                    pending_tool_idx = len(records) - 1
                    pending_tool_ts = dt
                else:
                    pending_tool_idx = None
                    pending_tool_ts = None

            else:
                if pending_tool_idx is not None and _has_tool_result(content):
                    delta = (dt - pending_tool_ts).total_seconds()
                    if 0 < delta < _MAX_SECONDS:
                        records[pending_tool_idx]["tool_batch_seconds"] = delta
                    pending_tool_idx = None
                    pending_tool_ts = None
                prev_user_ts = dt

    return records


def _build_db(records: list[dict]) -> duckdb.DuckDBPyConnection | None:
    if not records:
        return None

    table = pa.table({
        "session_id":          pa.array([r["session_id"] for r in records], type=pa.string()),
        "project":             pa.array([r["project"] for r in records], type=pa.string()),
        "source":              pa.array([r["source"] for r in records], type=pa.string()),
        "timestamp":           pa.array([r["timestamp"] for r in records], type=pa.string()),
        "model":               pa.array([r["model"] for r in records], type=pa.string()),
        "inference_seconds":   pa.array([r["inference_seconds"] for r in records], type=pa.float64()),
        "tool_batch_seconds":  pa.array([r["tool_batch_seconds"] for r in records], type=pa.float64()),
        "tool_names":          pa.array([r["tool_names"] for r in records], type=pa.list_(pa.string())),
        "tool_count":          pa.array([r["tool_count"] for r in records], type=pa.int32()),
        "input_tokens":        pa.array([r["input_tokens"] for r in records], type=pa.int64()),
        "output_tokens":       pa.array([r["output_tokens"] for r in records], type=pa.int64()),
        "cache_read_tokens":   pa.array([r["cache_read_tokens"] for r in records], type=pa.int64()),
        "cache_create_tokens": pa.array([r["cache_create_tokens"] for r in records], type=pa.int64()),
        "turn_number":         pa.array([r["turn_number"] for r in records], type=pa.int32()),
        "is_subagent":         pa.array([r["is_subagent"] for r in records], type=pa.bool_()),
    })

    conn = duckdb.connect()
    conn.register("_lat_raw", table)
    conn.execute("""
        CREATE TABLE latency AS
        SELECT
            session_id, project, source,
            timestamp::TIMESTAMP AS timestamp,
            model,
            inference_seconds,
            tool_batch_seconds,
            tool_names,
            tool_count,
            input_tokens, output_tokens,
            cache_read_tokens, cache_create_tokens,
            turn_number, is_subagent
        FROM _lat_raw
    """)
    return conn


def load_latency_records(files: list[Path]) -> duckdb.DuckDBPyConnection | None:
    return _build_db(_extract_records(files))


def load_latency_multi_source(
    source_files: list[tuple[str, Path]],
) -> duckdb.DuckDBPyConnection | None:
    by_source: dict[str, list[Path]] = {}
    for source_name, file_path in source_files:
        by_source.setdefault(source_name, []).append(file_path)

    all_records: list[dict] = []
    for source_name, files in by_source.items():
        all_records.extend(_extract_records(files, source=source_name))
    return _build_db(all_records)
