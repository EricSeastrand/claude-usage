"""Report formatting for Claude Code usage analysis."""

from collections import defaultdict
from datetime import datetime
from typing import Generator

import duckdb

from .pricing import compute_cost, get_pricing


def _fmt_tokens(n: int) -> str:
    """Format token count with K/M suffix."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_cost(dollars: float) -> str:
    """Format dollar amount."""
    if dollars >= 1.0:
        return f"${dollars:.2f}"
    return f"${dollars:.4f}"


def _table(headers: list[str], rows: list[list[str]], alignments: list[str] | None = None):
    """Print a simple aligned table.

    alignments: list of 'l' or 'r' per column. Default: first col left, rest right.
    """
    if not rows:
        print("  (no data)")
        return

    if alignments is None:
        alignments = ["l"] + ["r"] * (len(headers) - 1)

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    def _fmt_row(cells):
        parts = []
        for i, cell in enumerate(cells):
            if alignments[i] == "r":
                parts.append(cell.rjust(col_widths[i]))
            else:
                parts.append(cell.ljust(col_widths[i]))
        return "  ".join(parts)

    print(_fmt_row(headers))
    print("  ".join("-" * w for w in col_widths))
    for row in rows:
        print(_fmt_row(row))


def print_summary(conn: duckdb.DuckDBPyConnection):
    """Print aggregate usage summary with cost breakdown by model."""
    result = conn.execute("""
        SELECT
            model,
            COUNT(*) as api_calls,
            SUM(input_tokens) as input_tok,
            SUM(output_tokens) as output_tok,
            SUM(cache_write_5m_tokens) as cache_w5m,
            SUM(cache_write_1h_tokens) as cache_w1h,
            SUM(cache_read_tokens) as cache_r
        FROM usage
        GROUP BY model
        ORDER BY SUM(input_tokens + output_tokens) DESC
    """).fetchall()

    total_cost = 0.0
    total_input = 0
    total_output = 0
    total_cache_write = 0
    total_cache_read = 0
    total_calls = 0

    rows = []
    for model, calls, inp, out, cw5m, cw1h, cr in result:
        rates = get_pricing(model)
        cost = compute_cost(inp, out, cw5m, cw1h, cr, rates)
        total_cost += cost
        total_input += inp
        total_output += out
        total_cache_write += cw5m + cw1h
        total_cache_read += cr
        total_calls += calls
        rows.append([
            model,
            str(calls),
            _fmt_tokens(inp),
            _fmt_tokens(out),
            _fmt_tokens(cw5m + cw1h),
            _fmt_tokens(cr),
            _fmt_cost(cost),
        ])

    print(f"\n  Total API calls: {total_calls:,}")
    print(f"  Input tokens:    {_fmt_tokens(total_input)}")
    print(f"  Output tokens:   {_fmt_tokens(total_output)}")
    print(f"  Cache write:     {_fmt_tokens(total_cache_write)}")
    print(f"  Cache read:      {_fmt_tokens(total_cache_read)}")
    print(f"  Estimated cost:  {_fmt_cost(total_cost)}")
    print()

    headers = ["Model", "Calls", "Input", "Output", "Cache Write", "Cache Read", "Cost"]
    _table(headers, rows, ["l", "r", "r", "r", "r", "r", "r"])
    print()


def print_sessions(conn: duckdb.DuckDBPyConnection):
    """Print per-session usage table."""
    result = conn.execute("""
        SELECT
            session_id,
            project,
            MIN(timestamp) as started,
            model,
            COUNT(*) as api_calls,
            SUM(input_tokens) as input_tok,
            SUM(output_tokens) as output_tok,
            SUM(cache_write_5m_tokens) as cache_w5m,
            SUM(cache_write_1h_tokens) as cache_w1h,
            SUM(cache_read_tokens) as cache_r,
            FIRST(first_prompt) as prompt
        FROM usage
        GROUP BY session_id, project, model
        ORDER BY MIN(timestamp) DESC
    """).fetchall()

    rows = []
    total_cost = 0.0
    for sid, _proj, started, model, calls, inp, out, cw5m, cw1h, cr, prompt in result:
        rates = get_pricing(model)
        cost = compute_cost(inp, out, cw5m, cw1h, cr, rates)
        total_cost += cost

        time_str = started.strftime("%m-%d %H:%M") if started else "?"
        prompt_snippet = (prompt or "")[:50]
        if len(prompt or "") > 50:
            prompt_snippet += "..."

        rows.append([
            sid[:8],
            time_str,
            model.removeprefix("claude-"),
            str(calls),
            _fmt_tokens(inp + cw5m + cw1h + cr),
            _fmt_tokens(out),
            _fmt_cost(cost),
            prompt_snippet,
        ])

    print(f"\n  {len(result)} sessions, total cost: {_fmt_cost(total_cost)}\n")
    headers = ["Session", "Started", "Model", "Calls", "In+Cache", "Output", "Cost", "Prompt"]
    _table(headers, rows, ["l", "l", "l", "r", "r", "r", "r", "l"])
    print()


def print_daily(conn: duckdb.DuckDBPyConnection):
    """Print daily usage breakdown."""
    result = conn.execute("""
        SELECT
            CAST(timestamp AS DATE) as day,
            model,
            COUNT(*) as api_calls,
            COUNT(DISTINCT session_id) as sessions,
            SUM(input_tokens) as input_tok,
            SUM(output_tokens) as output_tok,
            SUM(cache_write_5m_tokens) as cache_w5m,
            SUM(cache_write_1h_tokens) as cache_w1h,
            SUM(cache_read_tokens) as cache_r
        FROM usage
        GROUP BY CAST(timestamp AS DATE), model
        ORDER BY day DESC, SUM(input_tokens + output_tokens) DESC
    """).fetchall()

    rows = []
    total_cost = 0.0
    for day, model, calls, sessions, inp, out, cw5m, cw1h, cr in result:
        rates = get_pricing(model)
        cost = compute_cost(inp, out, cw5m, cw1h, cr, rates)
        total_cost += cost
        rows.append([
            str(day),
            model.removeprefix("claude-"),
            str(sessions),
            str(calls),
            _fmt_tokens(inp),
            _fmt_tokens(out),
            _fmt_tokens(cw5m + cw1h),
            _fmt_tokens(cr),
            _fmt_cost(cost),
        ])

    print(f"\n  Total cost: {_fmt_cost(total_cost)}\n")
    headers = ["Date", "Model", "Sessions", "Calls", "Input", "Output", "Cache Write", "Cache Read", "Cost"]
    _table(headers, rows, ["l", "l", "r", "r", "r", "r", "r", "r", "r"])
    print()


def print_session_detail(conn: duckdb.DuckDBPyConnection, session_prefix: str):
    """Print detailed view of a single session."""
    # Find matching session
    matches = conn.execute(
        "SELECT DISTINCT session_id FROM usage WHERE session_id LIKE ?",
        [f"{session_prefix}%"],
    ).fetchall()

    if not matches:
        print(f"\n  No session found matching '{session_prefix}'")
        return
    if len(matches) > 1:
        print(f"\n  Multiple sessions match '{session_prefix}':")
        for (sid,) in matches[:10]:
            print(f"    {sid}")
        return

    session_id = matches[0][0]

    meta = conn.execute("""
        SELECT
            project,
            MIN(timestamp) as started,
            MAX(timestamp) as ended,
            FIRST(first_prompt) as prompt,
            model
        FROM usage WHERE session_id = ?
        GROUP BY session_id, project, model
    """, [session_id]).fetchone()

    project, started, ended, prompt, model = meta

    print(f"\n  Session:  {session_id}")
    print(f"  Project:  {project}")
    print(f"  Model:    {model}")
    print(f"  Started:  {started}")
    print(f"  Ended:    {ended}")
    print(f"  Prompt:   {prompt}")
    print()

    # Per-call breakdown
    result = conn.execute("""
        SELECT
            timestamp,
            input_tokens,
            output_tokens,
            cache_write_5m_tokens,
            cache_write_1h_tokens,
            cache_read_tokens
        FROM usage WHERE session_id = ?
        ORDER BY timestamp
    """, [session_id]).fetchall()

    rates = get_pricing(model)
    total_cost = 0.0
    rows = []
    for ts, inp, out, cw5m, cw1h, cr in result:
        cost = compute_cost(inp, out, cw5m, cw1h, cr, rates)
        total_cost += cost
        time_str = ts.strftime("%H:%M:%S") if ts else "?"
        rows.append([
            time_str,
            _fmt_tokens(inp),
            _fmt_tokens(out),
            _fmt_tokens(cw5m),
            _fmt_tokens(cw1h),
            _fmt_tokens(cr),
            _fmt_cost(cost),
        ])

    headers = ["Time", "Input", "Output", "CacheW 5m", "CacheW 1h", "Cache Read", "Cost"]
    _table(headers, rows)
    print(f"\n  Total: {len(result)} API calls, {_fmt_cost(total_cost)}")
    print()


def print_search(conn: duckdb.DuckDBPyConnection, keyword: str):
    """Search sessions by prompt text."""
    result = conn.execute("""
        SELECT
            session_id,
            project,
            MIN(timestamp) as started,
            model,
            COUNT(*) as api_calls,
            SUM(input_tokens) as input_tok,
            SUM(output_tokens) as output_tok,
            SUM(cache_write_5m_tokens + cache_write_1h_tokens) as cache_w,
            SUM(cache_read_tokens) as cache_r,
            FIRST(first_prompt) as prompt
        FROM usage
        WHERE lower(first_prompt) LIKE ?
        GROUP BY session_id, project, model
        ORDER BY MIN(timestamp) DESC
    """, [f"%{keyword.lower()}%"]).fetchall()

    if not result:
        print(f"\n  No sessions found matching '{keyword}'")
        return

    rows = []
    for sid, _proj, started, model, calls, inp, out, cw, cr, prompt in result:
        rates = get_pricing(model)
        cost = compute_cost(inp, out, cw, 0, cr, rates)
        time_str = started.strftime("%m-%d %H:%M") if started else "?"
        prompt_snippet = (prompt or "")[:60]
        if len(prompt or "") > 60:
            prompt_snippet += "..."
        rows.append([
            sid[:8],
            time_str,
            str(calls),
            _fmt_cost(cost),
            prompt_snippet,
        ])

    print(f"\n  {len(result)} sessions matching '{keyword}'\n")
    headers = ["Session", "Started", "Calls", "Cost", "Prompt"]
    _table(headers, rows, ["l", "l", "r", "r", "l"])
    print()


def _parse_ts(ts_str: str) -> datetime | None:
    """Parse an ISO 8601 timestamp string."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt_duration(start: datetime, end: datetime) -> str:
    """Format a duration between two datetimes."""
    delta = end - start
    total_sec = int(delta.total_seconds())
    if total_sec < 60:
        return f"{total_sec}s"
    if total_sec < 3600:
        return f"{total_sec // 60}m"
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    return f"{hours}h {minutes}m"


def print_timeline(
    session_id: str,
    project: str,
    messages: list[dict],
    full: bool = False,
):
    """Print a conversation timeline for a session."""
    if not messages:
        print(f"\n  No messages found in session {session_id[:8]}")
        return

    first_ts = _parse_ts(messages[0]["timestamp"])
    last_ts = _parse_ts(messages[-1]["timestamp"])

    print(f"\n  Session:  {session_id}")
    print(f"  Project:  {project}")
    if first_ts and last_ts:
        print(f"  Duration: {first_ts.strftime('%Y-%m-%d %H:%M')} -> "
              f"{last_ts.strftime('%H:%M')} ({_fmt_duration(first_ts, last_ts)})")
    print(f"  Messages: {len(messages)}")
    print()

    for msg in messages:
        ts = _parse_ts(msg["timestamp"])
        time_str = ts.strftime("%H:%M:%S") if ts else "??:??:??"
        role = msg["role"]
        text = msg["text"]
        tools = msg["tools"]

        role_tag = "[user]     " if role == "user" else "[assistant]"

        if full:
            # Show complete message text
            lines = text.split("\n")
            print(f"  {time_str}  {role_tag}  {lines[0]}")
            indent = " " * 24
            for line in lines[1:]:
                print(f"{indent}{line}")
        else:
            # Condensed: first ~120 chars on one line
            snippet = text.replace("\n", " ")[:120]
            if len(text) > 120:
                snippet += "..."
            print(f"  {time_str}  {role_tag}  {snippet}")

        # Show tool uses as indented lines
        if tools:
            indent = " " * 24
            for tool in tools:
                print(f"{indent}-> {tool}")

    print()


def print_grep_results(results: Generator[dict, None, None], pattern: str):
    """Print grep results grouped by session."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        grouped[r["session_id"]].append(r)

    if not grouped:
        print(f"\n  No matches for '{pattern}'")
        return

    total_matches = sum(len(v) for v in grouped.values())
    print(f"\n  {total_matches} matches in {len(grouped)} sessions\n")

    for session_id, matches in grouped.items():
        first = matches[0]
        ts = _parse_ts(first["timestamp"])
        ts_str = ts.strftime("%m-%d %H:%M") if ts else "?"
        print(f"  Session {session_id[:8]} ({ts_str}) {first['project']}")

        for m in matches:
            mts = _parse_ts(m["timestamp"])
            mts_str = mts.strftime("%H:%M:%S") if mts else "?"
            role_tag = f"[{m['role']}]"
            # Show snippet around match
            text = m["text"]
            start = max(0, m["match_start"] - 40)
            end = min(len(text), m["match_end"] + 40)
            snippet = text[start:end].replace("\n", " ")
            if start > 0:
                snippet = "..." + snippet
            if end < len(text):
                snippet = snippet + "..."
            print(f"    {mts_str}  {role_tag:13s}  {snippet}")

        print()
