"""Claude Code usage analyzer.

Analyzes token usage and estimates costs from Claude Code's local session
files (~/.claude/projects/). Uses DuckDB for fast SQL queries over JSONL.

Usage:
    .venv/bin/python -m claude_usage summary [--hours N | --date YYYY-MM-DD | --all]
    .venv/bin/python -m claude_usage sessions [--hours N | --date YYYY-MM-DD | --all]
    .venv/bin/python -m claude_usage session <id-prefix>
    .venv/bin/python -m claude_usage search <keyword>
    .venv/bin/python -m claude_usage daily [--hours N | --date YYYY-MM-DD | --all]
"""

import argparse
import sys

from .loader import discover_session_files, load_usage_records
from .reports import (
    print_daily,
    print_search,
    print_session_detail,
    print_sessions,
    print_summary,
)


def _add_time_flags(parser: argparse.ArgumentParser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--hours", type=int, default=24, help="Hours to look back (default: 24)")
    group.add_argument("--date", type=str, help="Specific date (YYYY-MM-DD)")
    group.add_argument("--all", action="store_true", help="All time, no filter")


def _resolve_time(args) -> dict:
    if getattr(args, "all", False):
        return {}
    if getattr(args, "date", None):
        return {"date": args.date}
    return {"hours": getattr(args, "hours", 24)}


def _load(args):
    time_kwargs = _resolve_time(args)
    files = discover_session_files(**time_kwargs)
    if not files:
        time_desc = "matching your filter" if time_kwargs else "at all"
        print(f"\n  No session files found {time_desc}.")
        sys.exit(0)

    conn = load_usage_records(files)
    if conn is None:
        print("\n  No usage records found in session files.")
        sys.exit(0)

    return conn


def main():
    parser = argparse.ArgumentParser(
        prog="claude_usage",
        description="Analyze Claude Code token usage and estimate costs.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # summary
    p_summary = subparsers.add_parser("summary", help="Aggregate usage + cost breakdown")
    _add_time_flags(p_summary)

    # sessions
    p_sessions = subparsers.add_parser("sessions", help="Per-session usage list")
    _add_time_flags(p_sessions)

    # session detail
    p_session = subparsers.add_parser("session", help="Detailed view of one session")
    p_session.add_argument("id", help="Session ID or prefix")

    # search
    p_search = subparsers.add_parser("search", help="Find sessions by prompt text")
    p_search.add_argument("keyword", help="Search term")

    # daily
    p_daily = subparsers.add_parser("daily", help="Daily usage breakdown")
    _add_time_flags(p_daily)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "session":
        # Session detail needs all files to find the session
        conn = load_usage_records(discover_session_files())
        if conn is None:
            print("\n  No usage records found.")
            sys.exit(0)
        print_session_detail(conn, args.id)
    elif args.command == "search":
        conn = load_usage_records(discover_session_files())
        if conn is None:
            print("\n  No usage records found.")
            sys.exit(0)
        print_search(conn, args.keyword)
    elif args.command == "summary":
        print_summary(_load(args))
    elif args.command == "sessions":
        print_sessions(_load(args))
    elif args.command == "daily":
        print_daily(_load(args))


if __name__ == "__main__":
    main()
