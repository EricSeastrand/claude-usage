"""Claude Code usage analyzer.

Analyzes token usage and estimates costs from Claude Code's local session
files (~/.claude/projects/). Uses DuckDB for fast SQL queries over JSONL.

Usage:
    .venv/bin/python -m claude_usage summary [--hours N | --date YYYY-MM-DD | --all] [--path DIR]
    .venv/bin/python -m claude_usage sessions [--hours N | --date YYYY-MM-DD | --all] [--path DIR]
    .venv/bin/python -m claude_usage session <id-prefix> [--path DIR]
    .venv/bin/python -m claude_usage search <keyword> [--path DIR]
    .venv/bin/python -m claude_usage daily [--hours N | --date YYYY-MM-DD | --all] [--path DIR]
    .venv/bin/python -m claude_usage timeline <id-prefix> [--full] [--path DIR]
    .venv/bin/python -m claude_usage grep <pattern> [--hours N | --date YYYY-MM-DD | --all] [--path DIR]
"""

import argparse
import sys

from .loader import (
    discover_session_files,
    find_session_file,
    grep_messages,
    load_session_messages,
    load_usage_records,
)
from .reports import (
    print_daily,
    print_grep_results,
    print_search,
    print_session_detail,
    print_sessions,
    print_summary,
    print_timeline,
)


def _add_common_flags(parser: argparse.ArgumentParser):
    parser.add_argument("--path", type=str, help="Path to Claude projects directory (default: ~/.claude/projects)")


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
    time_kwargs["path"] = getattr(args, "path", None)
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
    _add_common_flags(p_summary)

    # sessions
    p_sessions = subparsers.add_parser("sessions", help="Per-session usage list")
    _add_time_flags(p_sessions)
    _add_common_flags(p_sessions)

    # session detail
    p_session = subparsers.add_parser("session", help="Detailed view of one session")
    p_session.add_argument("id", help="Session ID or prefix")
    _add_common_flags(p_session)

    # search
    p_search = subparsers.add_parser("search", help="Find sessions by prompt text")
    p_search.add_argument("keyword", help="Search term")
    _add_common_flags(p_search)

    # daily
    p_daily = subparsers.add_parser("daily", help="Daily usage breakdown")
    _add_time_flags(p_daily)
    _add_common_flags(p_daily)

    # timeline
    p_timeline = subparsers.add_parser("timeline", help="Conversation timeline for a session")
    p_timeline.add_argument("id", help="Session ID or prefix")
    p_timeline.add_argument("--full", action="store_true", help="Show full message text")
    _add_common_flags(p_timeline)

    # grep
    p_grep = subparsers.add_parser("grep", help="Search all messages across sessions")
    p_grep.add_argument("pattern", help="Regex pattern to search for")
    _add_time_flags(p_grep)
    _add_common_flags(p_grep)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    custom_path = getattr(args, "path", None)

    if args.command == "session":
        conn = load_usage_records(discover_session_files(path=custom_path))
        if conn is None:
            print("\n  No usage records found.")
            sys.exit(0)
        print_session_detail(conn, args.id)
    elif args.command == "search":
        conn = load_usage_records(discover_session_files(path=custom_path))
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
    elif args.command == "timeline":
        session_file = find_session_file(args.id, path=custom_path)
        if session_file is None:
            sys.exit(1)
        messages = load_session_messages(session_file)
        print_timeline(
            session_id=session_file.stem,
            project=session_file.parent.name,
            messages=messages,
            full=args.full,
        )
    elif args.command == "grep":
        time_kwargs = _resolve_time(args)
        time_kwargs["path"] = custom_path
        files = discover_session_files(**time_kwargs)
        if not files:
            print("\n  No session files found matching your filter.")
            sys.exit(0)
        print_grep_results(grep_messages(files, args.pattern), args.pattern)


if __name__ == "__main__":
    main()
