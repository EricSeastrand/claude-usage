"""Analyze tool call result sizes from raw Claude Code session JSONL files."""

import json
import sys
from pathlib import Path
from collections import defaultdict

CLAUDE_DIR = Path.home() / ".claude" / "projects"


def estimate_tokens(text):
    """Rough token estimate: ~4 chars per token for English/code."""
    if not text:
        return 0
    return len(text) // 4


def extract_tool_results(files):
    """Extract all tool results with their sizes."""
    results = []

    for path in files:
        session_id = path.stem[:8]

        with open(path) as f:
            # We need to correlate tool_use (from assistant) with tool_result (from user)
            # Assistant messages contain tool_use blocks with IDs
            # The next user message contains tool_result blocks with matching IDs
            pending_tools = {}  # tool_use_id -> {name, input_summary}

            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rec_type = rec.get("type")

                if rec_type == "assistant":
                    content = rec.get("message", {}).get("content", [])
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tool_id = block.get("id", "")
                            tool_name = block.get("name", "unknown")
                            tool_input = block.get("input", {})
                            # Summarize the input
                            input_summary = ""
                            if isinstance(tool_input, dict):
                                if "command" in tool_input:
                                    input_summary = tool_input["command"][:120]
                                elif "file_path" in tool_input:
                                    input_summary = tool_input["file_path"]
                                elif "pattern" in tool_input:
                                    input_summary = f"pattern={tool_input['pattern']}"
                                elif "query" in tool_input:
                                    input_summary = f"query={str(tool_input['query'])[:80]}"
                                elif "prompt" in tool_input:
                                    input_summary = f"prompt={str(tool_input.get('prompt',''))[:80]}"
                                else:
                                    input_summary = json.dumps(tool_input)[:120]

                            pending_tools[tool_id] = {
                                "name": tool_name,
                                "input_summary": input_summary,
                            }

                elif rec_type == "user":
                    content = rec.get("message", {}).get("content", [])
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            tool_id = block.get("tool_use_id", "")
                            tool_info = pending_tools.pop(tool_id, {"name": "unknown", "input_summary": ""})

                            # Measure the result content
                            result_content = block.get("content", "")
                            if isinstance(result_content, list):
                                # Multi-block result
                                total_text = ""
                                for rb in result_content:
                                    if isinstance(rb, dict) and rb.get("type") == "text":
                                        total_text += rb.get("text", "")
                                result_text = total_text
                            elif isinstance(result_content, str):
                                result_text = result_content
                            else:
                                result_text = json.dumps(result_content)

                            char_len = len(result_text)
                            est_tokens = estimate_tokens(result_text)

                            results.append({
                                "session_id": session_id,
                                "tool_name": tool_info["name"],
                                "input_summary": tool_info["input_summary"],
                                "char_len": char_len,
                                "est_tokens": est_tokens,
                                "result_preview": result_text[:150].replace('\n', '\\n'),
                            })

    return results


def fmt(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def main():
    all_files = sorted(CLAUDE_DIR.glob("*/*.jsonl"))
    print(f"Scanning {len(all_files)} session files...")

    results = extract_tool_results(all_files)
    print(f"Found {len(results)} tool results total.\n")

    if not results:
        return

    # === 1. Size distribution ===
    print("=" * 70)
    print("TOOL RESULT SIZE DISTRIBUTION")
    print("=" * 70)

    brackets = [
        (0, 100, "<100 chars"),
        (100, 500, "100-500"),
        (500, 1_000, "500-1K"),
        (1_000, 5_000, "1-5K"),
        (5_000, 10_000, "5-10K"),
        (10_000, 50_000, "10-50K"),
        (50_000, 100_000, "50-100K"),
        (100_000, 500_000, "100-500K"),
        (500_000, 999_999_999, ">500K"),
    ]

    print(f"\n  {'Size bracket':>15}  {'Count':>7}  {'% of calls':>9}  {'Total chars':>12}  {'% of payload':>11}")
    print(f"  {'-'*15}  {'-'*7}  {'-'*9}  {'-'*12}  {'-'*11}")

    grand_total_chars = sum(r["char_len"] for r in results)
    for lo, hi, label in brackets:
        matching = [r for r in results if lo <= r["char_len"] < hi]
        count = len(matching)
        total_chars = sum(r["char_len"] for r in matching)
        if count > 0:
            pct_calls = 100 * count / len(results)
            pct_payload = 100 * total_chars / grand_total_chars
            print(f"  {label:>15}  {count:>7,}  {pct_calls:>8.1f}%  {fmt(total_chars):>12}  {pct_payload:>10.1f}%")

    print(f"\n  Grand total: {len(results):,} tool calls, {fmt(grand_total_chars)} chars (~{fmt(grand_total_chars//4)} tokens)")

    # === 2. By tool name ===
    print(f"\n{'=' * 70}")
    print("BY TOOL NAME (sorted by total payload)")
    print("=" * 70)

    by_tool = defaultdict(lambda: {"count": 0, "total_chars": 0, "max_chars": 0, "max_input": ""})
    for r in results:
        name = r["tool_name"]
        by_tool[name]["count"] += 1
        by_tool[name]["total_chars"] += r["char_len"]
        if r["char_len"] > by_tool[name]["max_chars"]:
            by_tool[name]["max_chars"] = r["char_len"]
            by_tool[name]["max_input"] = r["input_summary"]

    sorted_tools = sorted(by_tool.items(), key=lambda x: x[1]["total_chars"], reverse=True)
    print(f"\n  {'Tool':>35}  {'Count':>7}  {'Total':>8}  {'Avg':>7}  {'Max':>8}  {'Max call':>40}")
    print(f"  {'-'*35}  {'-'*7}  {'-'*8}  {'-'*7}  {'-'*8}  {'-'*40}")
    for name, stats in sorted_tools[:25]:
        avg = stats["total_chars"] // stats["count"] if stats["count"] else 0
        print(f"  {name:>35}  {stats['count']:>7,}  {fmt(stats['total_chars']):>8}  {fmt(avg):>7}  {fmt(stats['max_chars']):>8}  {stats['max_input'][:40]}")

    # === 3. Biggest individual tool results ===
    print(f"\n{'=' * 70}")
    print("TOP 40 BIGGEST INDIVIDUAL TOOL RESULTS")
    print("=" * 70)

    by_size = sorted(results, key=lambda r: r["char_len"], reverse=True)[:40]
    for r in by_size:
        print(f"\n  {r['session_id']}  {r['tool_name']}  {fmt(r['char_len'])} chars (~{fmt(r['est_tokens'])} tokens)")
        print(f"    Input: {r['input_summary'][:90]}")
        print(f"    Preview: {r['result_preview'][:120]}")

    # === 4. "Noise" analysis: repeated similar tool calls with large results ===
    print(f"\n{'=' * 70}")
    print("POTENTIAL NOISE: TOOL+INPUT PATTERNS WITH LARGEST TOTAL PAYLOAD")
    print("=" * 70)
    print("  (Same tool+similar input called repeatedly with big results)")

    # Group by tool_name + first 60 chars of input
    by_pattern = defaultdict(lambda: {"count": 0, "total_chars": 0, "max_chars": 0})
    for r in results:
        key = f"{r['tool_name']}:{r['input_summary'][:60]}"
        by_pattern[key]["count"] += 1
        by_pattern[key]["total_chars"] += r["char_len"]
        by_pattern[key]["max_chars"] = max(by_pattern[key]["max_chars"], r["char_len"])

    # Only show patterns called 3+ times with meaningful payload
    noisy = [(k, v) for k, v in by_pattern.items() if v["count"] >= 3 and v["total_chars"] > 50000]
    noisy.sort(key=lambda x: x[1]["total_chars"], reverse=True)

    for pattern, stats in noisy[:20]:
        avg = stats["total_chars"] // stats["count"]
        print(f"\n  {pattern[:80]}")
        print(f"    {stats['count']} calls, total {fmt(stats['total_chars'])}, avg {fmt(avg)}, max {fmt(stats['max_chars'])}")


if __name__ == "__main__":
    main()
