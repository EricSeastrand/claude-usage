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


# ---------------------------------------------------------------------------
# New reports: compaction, context growth, efficiency, sources
# ---------------------------------------------------------------------------


def print_compactions(conn: duckdb.DuckDBPyConnection):
    """Print sessions that hit compaction, with context stats."""
    result = conn.execute("""
        WITH comp_stats AS (
            SELECT
                c.session_id,
                c.project,
                COUNT(*) as num_compactions,
                MIN(c.turn_number) as first_compaction_turn,
                MAX(c.pre_tokens) as max_pre_tokens,
                MIN(c.timestamp) as first_compaction_ts
            FROM compactions c
            GROUP BY c.session_id, c.project
        ),
        session_stats AS (
            SELECT
                session_id,
                project,
                MAX(turn_number) as total_turns,
                MAX(effective_context) as peak_context,
                SUM(input_tokens) as input_tok,
                SUM(output_tokens) as output_tok,
                SUM(cache_write_5m_tokens) as cw5m,
                SUM(cache_write_1h_tokens) as cw1h,
                SUM(cache_read_tokens) as cr,
                FIRST(model) as model,
                FIRST(first_prompt) as prompt
            FROM usage
            WHERE NOT is_subagent
            GROUP BY session_id, project
        )
        SELECT
            cs.session_id,
            cs.project,
            cs.num_compactions,
            cs.first_compaction_turn,
            cs.max_pre_tokens,
            ss.total_turns,
            ss.peak_context,
            ss.model,
            ss.input_tok, ss.output_tok, ss.cw5m, ss.cw1h, ss.cr,
            ss.prompt
        FROM comp_stats cs
        JOIN session_stats ss ON cs.session_id = ss.session_id
        ORDER BY cs.num_compactions DESC, cs.max_pre_tokens DESC
    """).fetchall()

    if not result:
        print("\n  No compaction events found.")
        return

    total_compactions = sum(r[2] for r in result)
    print(f"\n  {len(result)} sessions with compaction ({total_compactions} total events)\n")

    rows = []
    for (sid, proj, n_comp, first_turn, max_pre, total_turns,
         peak_ctx, model, inp, out, cw5m, cw1h, cr, prompt) in result:
        rates = get_pricing(model)
        cost = compute_cost(inp, out, cw5m, cw1h, cr, rates)
        prompt_snippet = (prompt or "")[:40]
        if len(prompt or "") > 40:
            prompt_snippet += "..."
        rows.append([
            sid[:8],
            str(n_comp),
            str(first_turn),
            str(total_turns),
            _fmt_tokens(max_pre),
            _fmt_tokens(peak_ctx),
            _fmt_cost(cost),
            prompt_snippet,
        ])

    headers = ["Session", "Compacts", "1st@Turn", "Turns", "MaxPre", "PeakCtx", "Cost", "Prompt"]
    _table(headers, rows, ["l", "r", "r", "r", "r", "r", "r", "l"])
    print()


def print_context_growth(conn: duckdb.DuckDBPyConnection, session_prefix: str):
    """Print per-turn context growth for a session, with compaction markers."""
    matches = conn.execute(
        "SELECT DISTINCT session_id FROM usage WHERE session_id LIKE ? AND NOT is_subagent",
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

    # Get compaction turn numbers for this session
    comp_turns = set()
    comp_rows = conn.execute(
        "SELECT turn_number FROM compactions WHERE session_id = ?",
        [session_id],
    ).fetchall()
    for (t,) in comp_rows:
        comp_turns.add(t)

    result = conn.execute("""
        SELECT
            turn_number,
            timestamp,
            effective_context,
            input_tokens,
            cache_read_tokens,
            output_tokens,
            model
        FROM usage
        WHERE session_id = ? AND NOT is_subagent
        ORDER BY turn_number
    """, [session_id]).fetchall()

    if not result:
        print(f"\n  No usage records for session {session_id[:8]}")
        return

    model = result[0][6]
    peak_ctx = max(r[2] for r in result)

    print(f"\n  Session: {session_id}")
    print(f"  Model:   {model}")
    print(f"  Turns:   {len(result)}")
    print(f"  Peak:    {_fmt_tokens(peak_ctx)}")
    if comp_turns:
        print(f"  Compactions: {len(comp_turns)} (at turns {', '.join(str(t) for t in sorted(comp_turns))})")
    print()

    # Bar chart: context size per turn
    max_bar = 50
    rows = []
    for turn, ts, eff_ctx, inp, cr, out, _model in result:
        time_str = ts.strftime("%H:%M:%S") if ts else "?"
        bar_len = int((eff_ctx / peak_ctx) * max_bar) if peak_ctx > 0 else 0
        bar = "#" * bar_len

        # Mark compaction boundaries
        marker = " <<< COMPACTED" if turn in comp_turns else ""

        # Warn when >80% of peak
        if eff_ctx > peak_ctx * 0.8 and not marker:
            marker = " !"

        rows.append(f"  {turn:3d}  {time_str}  {_fmt_tokens(eff_ctx):>6s}  {bar}{marker}")

    for row in rows:
        print(row)
    print()


def print_efficiency(conn: duckdb.DuckDBPyConnection):
    """Print aggregate efficiency metrics."""
    # Total sessions (non-subagent)
    total_sessions = conn.execute(
        "SELECT COUNT(DISTINCT session_id) FROM usage WHERE NOT is_subagent"
    ).fetchone()[0]

    # Sessions with compaction
    compacted_sessions = conn.execute(
        "SELECT COUNT(DISTINCT session_id) FROM compactions"
    ).fetchone()[0]

    # Average turns before first compaction
    avg_turns_to_compact = conn.execute("""
        SELECT AVG(first_turn) FROM (
            SELECT session_id, MIN(turn_number) as first_turn
            FROM compactions
            GROUP BY session_id
        )
    """).fetchone()[0]

    # Total cost breakdown: parent vs subagent
    parent_cost_data = conn.execute("""
        SELECT
            SUM(input_tokens) as inp,
            SUM(output_tokens) as out,
            SUM(cache_write_5m_tokens) as cw5m,
            SUM(cache_write_1h_tokens) as cw1h,
            SUM(cache_read_tokens) as cr,
            FIRST(model) as model
        FROM usage WHERE NOT is_subagent
    """).fetchone()

    sub_cost_data = conn.execute("""
        SELECT
            SUM(input_tokens) as inp,
            SUM(output_tokens) as out,
            SUM(cache_write_5m_tokens) as cw5m,
            SUM(cache_write_1h_tokens) as cw1h,
            SUM(cache_read_tokens) as cr,
            FIRST(model) as model
        FROM usage WHERE is_subagent
    """).fetchone()

    # Cost per user turn (non-subagent sessions)
    user_turns = conn.execute("""
        SELECT COUNT(DISTINCT session_id || '-' || turn_number)
        FROM usage WHERE NOT is_subagent
    """).fetchone()[0]

    def _safe_cost(row):
        if not row or row[0] is None:
            return 0.0
        rates = get_pricing(row[5] or "unknown")
        return compute_cost(row[0], row[1], row[2], row[3], row[4], rates)

    parent_cost = _safe_cost(parent_cost_data)
    sub_cost = _safe_cost(sub_cost_data)
    total_cost = parent_cost + sub_cost

    compaction_rate = (compacted_sessions / total_sessions * 100) if total_sessions > 0 else 0
    cost_per_turn = (parent_cost / user_turns) if user_turns > 0 else 0
    sub_pct = (sub_cost / total_cost * 100) if total_cost > 0 else 0

    print(f"\n  Sessions:              {total_sessions}")
    print(f"  With compaction:       {compacted_sessions} ({compaction_rate:.1f}%)")
    if avg_turns_to_compact:
        print(f"  Avg turns to compact:  {avg_turns_to_compact:.0f}")
    print(f"  Total cost:            {_fmt_cost(total_cost)}")
    print(f"    Parent sessions:     {_fmt_cost(parent_cost)}")
    print(f"    Subagents:           {_fmt_cost(sub_cost)} ({sub_pct:.1f}%)")
    print(f"  Cost per turn:         {_fmt_cost(cost_per_turn)}")
    print()

    # Weekly trend
    weekly = conn.execute("""
        SELECT
            DATE_TRUNC('week', timestamp)::DATE as week,
            COUNT(DISTINCT session_id) as sessions,
            COUNT(DISTINCT CASE WHEN session_id IN (
                SELECT DISTINCT session_id FROM compactions
            ) THEN session_id END) as compacted,
            SUM(input_tokens) as inp,
            SUM(output_tokens) as out,
            SUM(cache_write_5m_tokens) as cw5m,
            SUM(cache_write_1h_tokens) as cw1h,
            SUM(cache_read_tokens) as cr,
            FIRST(model) as model
        FROM usage
        WHERE NOT is_subagent
        GROUP BY DATE_TRUNC('week', timestamp)::DATE
        ORDER BY week DESC
        LIMIT 8
    """).fetchall()

    if weekly:
        print("  Weekly trend:\n")
        rows = []
        for week, sessions, compacted, inp, out, cw5m, cw1h, cr, model in weekly:
            rates = get_pricing(model or "unknown")
            cost = compute_cost(inp, out, cw5m, cw1h, cr, rates)
            comp_rate = f"{compacted/sessions*100:.0f}%" if sessions > 0 else "0%"
            rows.append([
                str(week),
                str(sessions),
                f"{compacted} ({comp_rate})",
                _fmt_cost(cost),
            ])
        headers = ["Week", "Sessions", "Compacted", "Cost"]
        _table(headers, rows, ["l", "r", "r", "r"])
        print()


def print_sources(conn: duckdb.DuckDBPyConnection):
    """Print breakdown by source."""
    result = conn.execute("""
        SELECT
            source,
            COUNT(DISTINCT session_id) as sessions,
            COUNT(*) as api_calls,
            SUM(input_tokens) as inp,
            SUM(output_tokens) as out,
            SUM(cache_write_5m_tokens) as cw5m,
            SUM(cache_write_1h_tokens) as cw1h,
            SUM(cache_read_tokens) as cr,
            FIRST(model) as model,
            SUM(CASE WHEN is_subagent THEN 1 ELSE 0 END) as subagent_calls
        FROM usage
        GROUP BY source
        ORDER BY SUM(input_tokens + output_tokens) DESC
    """).fetchall()

    if not result:
        print("\n  No data.")
        return

    total_cost = 0.0
    rows = []
    for source, sessions, calls, inp, out, cw5m, cw1h, cr, model, sub_calls in result:
        rates = get_pricing(model or "unknown")
        cost = compute_cost(inp, out, cw5m, cw1h, cr, rates)
        total_cost += cost
        rows.append([
            source,
            str(sessions),
            str(calls),
            str(sub_calls),
            _fmt_tokens(inp + cw5m + cw1h + cr),
            _fmt_tokens(out),
            _fmt_cost(cost),
        ])

    print(f"\n  Total cost across sources: {_fmt_cost(total_cost)}\n")
    headers = ["Source", "Sessions", "Calls", "Subagent", "In+Cache", "Output", "Cost"]
    _table(headers, rows, ["l", "r", "r", "r", "r", "r", "r"])
    print()
