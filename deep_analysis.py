"""Deep analysis of token patterns — where are tokens actually going?"""

import argparse
import json
from pathlib import Path
from collections import defaultdict

CLAUDE_DIR = Path.home() / ".claude" / "projects"


def analyze_all_sessions(claude_dir):
    """Analyze per-turn token patterns across all sessions."""
    all_files = sorted(claude_dir.glob("*/*.jsonl"))

    session_stats = []
    biggest_single_turns = []

    for path in all_files:
        session_id = path.stem
        project = path.parent.name
        first_prompt = None
        turns = []

        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rec_type = rec.get("type")

                if rec_type == "user" and first_prompt is None:
                    content = rec.get("message", {}).get("content", "")
                    if isinstance(content, str) and content and not content.startswith("<"):
                        first_prompt = content[:120]
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text = part["text"]
                                if text and not text.startswith("<"):
                                    first_prompt = text[:120]
                                    break

                if rec_type != "assistant":
                    continue

                msg = rec.get("message", {})
                usage = msg.get("usage")
                if not usage:
                    continue

                input_tok = usage.get("input_tokens", 0)
                output_tok = usage.get("output_tokens", 0)
                cache_write_5m = usage.get("cache_creation", {}).get("ephemeral_5m_input_tokens", 0)
                cache_write_1h = usage.get("cache_creation", {}).get("ephemeral_1h_input_tokens", 0)
                cache_read = usage.get("cache_read_input_tokens", 0)

                total = input_tok + output_tok + cache_write_5m + cache_write_1h + cache_read
                if total == 0:
                    continue

                # Total context = what the model actually "saw" this turn
                context_size = input_tok + cache_write_5m + cache_write_1h + cache_read

                turns.append({
                    "timestamp": rec.get("timestamp", ""),
                    "input_tokens": input_tok,
                    "output_tokens": output_tok,
                    "cache_write_5m": cache_write_5m,
                    "cache_write_1h": cache_write_1h,
                    "cache_read": cache_read,
                    "context_size": context_size,
                })

                biggest_single_turns.append({
                    "session_id": session_id[:8],
                    "project": project,
                    "prompt": first_prompt or "(no prompt)",
                    "context_size": context_size,
                    "input_tokens": input_tok,
                    "output_tokens": output_tok,
                    "cache_read": cache_read,
                    "cache_write": cache_write_5m + cache_write_1h,
                    "timestamp": rec.get("timestamp", ""),
                })

        if len(turns) < 2:
            continue

        # Compute session-level stats
        context_sizes = [t["context_size"] for t in turns]
        max_context = max(context_sizes)
        first_context = context_sizes[0]
        last_context = context_sizes[-1]

        # Growth: how much did the context window grow from first to peak?
        growth = max_context - first_context

        # Biggest single-turn jump
        max_jump = 0
        max_jump_idx = 0
        for i in range(1, len(context_sizes)):
            jump = context_sizes[i] - context_sizes[i-1]
            if jump > max_jump:
                max_jump = jump
                max_jump_idx = i

        total_input = sum(t["input_tokens"] for t in turns)
        total_output = sum(t["output_tokens"] for t in turns)
        total_cache_read = sum(t["cache_read"] for t in turns)
        total_cache_write = sum(t["cache_write_5m"] + t["cache_write_1h"] for t in turns)

        session_stats.append({
            "session_id": session_id[:8],
            "project": project,
            "prompt": first_prompt or "(no prompt)",
            "num_turns": len(turns),
            "first_context": first_context,
            "max_context": max_context,
            "last_context": last_context,
            "growth": growth,
            "max_jump": max_jump,
            "max_jump_idx": max_jump_idx,
            "total_input": total_input,
            "total_output": total_output,
            "total_cache_read": total_cache_read,
            "total_cache_write": total_cache_write,
        })

    return session_stats, biggest_single_turns


def fmt(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def main():
    parser = argparse.ArgumentParser(description="Deep analysis of token usage patterns")
    parser.add_argument("--path", type=str, help="Path to Claude projects directory (default: ~/.claude/projects)")
    args = parser.parse_args()

    claude_dir = Path(args.path) if args.path else CLAUDE_DIR
    print(f"Scanning: {claude_dir}")

    session_stats, biggest_turns = analyze_all_sessions(claude_dir)

    # === 1. Overall pattern analysis ===
    print("\n" + "="*70)
    print("TOKEN USAGE PATTERN ANALYSIS")
    print("="*70)

    # How many sessions hit large context windows?
    print("\n--- Context Window Size Distribution (peak per session) ---")
    brackets = [
        (0, 50_000, "<50K"),
        (50_000, 100_000, "50-100K"),
        (100_000, 200_000, "100-200K"),
        (200_000, 500_000, "200-500K"),
        (500_000, 1_000_000, "500K-1M"),
        (1_000_000, 2_000_000, "1-2M"),
        (2_000_000, 5_000_000, "2-5M"),
        (5_000_000, 999_000_000, ">5M"),
    ]
    for lo, hi, label in brackets:
        count = sum(1 for s in session_stats if lo <= s["max_context"] < hi)
        if count > 0:
            print(f"  {label:>10}: {count:3d} sessions")

    # === 2. Biggest context windows ever ===
    print("\n--- Top 20 Sessions by Peak Context Size ---")
    by_peak = sorted(session_stats, key=lambda s: s["max_context"], reverse=True)[:20]
    for s in by_peak:
        print(f"  {s['session_id']}  peak={fmt(s['max_context']):>6}  turns={s['num_turns']:>4}  growth={fmt(s['growth']):>6}  {s['prompt'][:60]}")

    # === 3. Sessions with biggest growth (context bloat) ===
    print("\n--- Top 20 Sessions by Context Growth (first->peak) ---")
    by_growth = sorted(session_stats, key=lambda s: s["growth"], reverse=True)[:20]
    for s in by_growth:
        print(f"  {s['session_id']}  first={fmt(s['first_context']):>6}  peak={fmt(s['max_context']):>6}  growth={fmt(s['growth']):>6}  turns={s['num_turns']:>4}  {s['prompt'][:50]}")

    # === 4. Biggest single-turn jumps ===
    print("\n--- Top 20 Biggest Single-Turn Context Jumps ---")
    by_jump = sorted(session_stats, key=lambda s: s["max_jump"], reverse=True)[:20]
    for s in by_jump:
        print(f"  {s['session_id']}  jump={fmt(s['max_jump']):>6}  at_turn={s['max_jump_idx']:>3}/{s['num_turns']}  peak={fmt(s['max_context']):>6}  {s['prompt'][:50]}")

    # === 5. Biggest individual API calls ===
    print("\n--- Top 30 Single API Calls by Context Size ---")
    by_context = sorted(biggest_turns, key=lambda t: t["context_size"], reverse=True)[:30]
    for t in by_context:
        print(f"  {t['session_id']}  ctx={fmt(t['context_size']):>6}  in={fmt(t['input_tokens']):>6}  cacheR={fmt(t['cache_read']):>6}  cacheW={fmt(t['cache_write']):>6}  out={fmt(t['output_tokens']):>6}  {t['prompt'][:45]}")

    # === 6. Cost decomposition: cache read dominates? ===
    print("\n--- Aggregate Token Breakdown ---")
    total_input = sum(s["total_input"] for s in session_stats)
    total_output = sum(s["total_output"] for s in session_stats)
    total_cache_read = sum(s["total_cache_read"] for s in session_stats)
    total_cache_write = sum(s["total_cache_write"] for s in session_stats)
    grand = total_input + total_output + total_cache_read + total_cache_write

    print(f"  Cache read:   {fmt(total_cache_read):>8}  ({100*total_cache_read/grand:.1f}% of all tokens)")
    print(f"  Cache write:  {fmt(total_cache_write):>8}  ({100*total_cache_write/grand:.1f}% of all tokens)")
    print(f"  Input (new):  {fmt(total_input):>8}  ({100*total_input/grand:.1f}% of all tokens)")
    print(f"  Output:       {fmt(total_output):>8}  ({100*total_output/grand:.1f}% of all tokens)")
    print(f"  Grand total:  {fmt(grand):>8}")

    # Cost breakdown
    # Opus 4.6 pricing: input $5/M, output $25/M, cache_read $0.50/M, cache_write_5m $6.25/M
    cost_input = total_input * 5.0 / 1_000_000
    cost_output = total_output * 25.0 / 1_000_000
    cost_cache_read = total_cache_read * 0.50 / 1_000_000
    cost_cache_write = total_cache_write * 6.25 / 1_000_000  # approximate
    total_cost = cost_input + cost_output + cost_cache_read + cost_cache_write

    print(f"\n--- Approximate Cost Breakdown ---")
    print(f"  Cache read:   ${cost_cache_read:>8.2f}  ({100*cost_cache_read/total_cost:.1f}% of cost)")
    print(f"  Cache write:  ${cost_cache_write:>8.2f}  ({100*cost_cache_write/total_cost:.1f}% of cost)")
    print(f"  Input (new):  ${cost_input:>8.2f}  ({100*cost_input/total_cost:.1f}% of cost)")
    print(f"  Output:       ${cost_output:>8.2f}  ({100*cost_output/total_cost:.1f}% of cost)")
    print(f"  Total:        ${total_cost:>8.2f}")

    # === 7. Session length distribution ===
    print("\n--- Session Length Distribution (turns) ---")
    turn_brackets = [
        (1, 1, "1 turn"),
        (2, 10, "2-10"),
        (11, 50, "11-50"),
        (51, 100, "51-100"),
        (101, 200, "101-200"),
        (201, 500, "201-500"),
        (501, 999999, ">500"),
    ]
    for lo, hi, label in turn_brackets:
        sessions = [s for s in session_stats if lo <= s["num_turns"] <= hi]
        count = len(sessions)
        total_cr = sum(s["total_cache_read"] for s in sessions)
        if count > 0:
            print(f"  {label:>10}: {count:3d} sessions, cache_read={fmt(total_cr)}")

    # === 8. Average context per turn (efficiency metric) ===
    print("\n--- Average Context Per Turn (sessions >10 turns) ---")
    long_sessions = [s for s in session_stats if s["num_turns"] > 10]
    long_sessions.sort(key=lambda s: s["total_cache_read"] / s["num_turns"], reverse=True)
    print("  (Higher = more context re-read per turn = more expensive per turn)")
    for s in long_sessions[:15]:
        avg_ctx = s["total_cache_read"] / s["num_turns"]
        print(f"  {s['session_id']}  avg_ctx/turn={fmt(int(avg_ctx)):>6}  turns={s['num_turns']:>4}  peak={fmt(s['max_context']):>6}  {s['prompt'][:45]}")


if __name__ == "__main__":
    main()
