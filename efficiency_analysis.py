"""Efficiency analysis - how much of total spend is re-reading the same context?"""

import json
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude" / "projects"


def analyze():
    all_files = sorted(CLAUDE_DIR.glob("*/*.jsonl"))
    
    sessions_hitting_ceiling = 0
    sessions_with_compression = 0
    total_sessions = 0
    
    # Per-project breakdown
    project_costs = {}
    
    # Compression events
    total_compressions = 0
    
    # "Wasted" cache reads - turns where output was <10 tokens (likely just tool calls)
    trivial_output_cache_read = 0
    total_cache_read = 0
    trivial_turn_count = 0
    total_turn_count = 0
    
    for path in all_files:
        session_id = path.stem
        project = path.parent.name
        turns = []
        first_prompt = None
        
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
                        first_prompt = content[:100]
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text = part["text"]
                                if text and not text.startswith("<"):
                                    first_prompt = text[:100]
                                    break
                
                if rec_type != "assistant":
                    continue
                
                msg = rec.get("message", {})
                usage = msg.get("usage")
                if not usage:
                    continue
                
                input_tok = usage.get("input_tokens", 0)
                cache_write_5m = usage.get("cache_creation", {}).get("ephemeral_5m_input_tokens", 0)
                cache_write_1h = usage.get("cache_creation", {}).get("ephemeral_1h_input_tokens", 0)
                cache_read = usage.get("cache_read_input_tokens", 0)
                output_tok = usage.get("output_tokens", 0)
                
                context_size = input_tok + cache_write_5m + cache_write_1h + cache_read
                if context_size == 0:
                    continue
                
                turns.append({
                    "context": context_size,
                    "output": output_tok,
                    "cache_read": cache_read,
                    "cache_write": cache_write_5m + cache_write_1h,
                    "input": input_tok,
                })
                
                total_cache_read += cache_read
                total_turn_count += 1
                if output_tok < 10:
                    trivial_output_cache_read += cache_read
                    trivial_turn_count += 1
                
                if project not in project_costs:
                    project_costs[project] = {"cache_read": 0, "cache_write": 0, "input": 0, "output": 0, "turns": 0, "sessions": set()}
                project_costs[project]["cache_read"] += cache_read
                project_costs[project]["cache_write"] += cache_write_5m + cache_write_1h
                project_costs[project]["input"] += input_tok
                project_costs[project]["output"] += output_tok
                project_costs[project]["turns"] += 1
                project_costs[project]["sessions"].add(session_id)
        
        if len(turns) < 2:
            continue
        
        total_sessions += 1
        
        # Did it hit the ~167K ceiling?
        max_ctx = max(t["context"] for t in turns)
        if max_ctx > 160_000:
            sessions_hitting_ceiling += 1
        
        # Did it get compressed?
        for i in range(1, len(turns)):
            if turns[i]["context"] < turns[i-1]["context"] - 20000:
                sessions_with_compression += 1
                break
        
        # Count compressions
        for i in range(1, len(turns)):
            if turns[i]["context"] < turns[i-1]["context"] - 20000:
                total_compressions += 1
    
    print("\n" + "="*70)
    print("EFFICIENCY ANALYSIS")
    print("="*70)
    
    print(f"\n--- Session Ceiling & Compression ---")
    print(f"  Total sessions (>1 turn): {total_sessions}")
    print(f"  Sessions hitting ~167K ceiling: {sessions_hitting_ceiling} ({100*sessions_hitting_ceiling/total_sessions:.0f}%)")
    print(f"  Sessions with compression events: {sessions_with_compression} ({100*sessions_with_compression/total_sessions:.0f}%)")
    print(f"  Total compression events: {total_compressions}")
    
    print(f"\n--- Trivial Output Turns (assistant output <10 tokens) ---")
    print(f"  These are turns where the model read the entire context but produced almost nothing")
    print(f"  (usually tool_use calls that just invoke a tool)")
    print(f"  Trivial turns: {trivial_turn_count} of {total_turn_count} ({100*trivial_turn_count/total_turn_count:.0f}%)")
    print(f"  Cache read on trivial turns: {trivial_output_cache_read/1_000_000:.1f}M tokens ({100*trivial_output_cache_read/total_cache_read:.0f}% of all cache reads)")
    print(f"  Cost of trivial-turn cache reads: ${trivial_output_cache_read * 0.50 / 1_000_000:.2f}")
    
    print(f"\n--- Per-Project Breakdown ---")
    sorted_projects = sorted(project_costs.items(), key=lambda x: x[1]["cache_read"], reverse=True)
    for proj, stats in sorted_projects:
        cr_cost = stats["cache_read"] * 0.50 / 1_000_000
        cw_cost = stats["cache_write"] * 6.25 / 1_000_000
        out_cost = stats["output"] * 25.0 / 1_000_000
        total = cr_cost + cw_cost + out_cost
        n_sessions = len(stats["sessions"])
        print(f"  {proj}")
        print(f"    Sessions: {n_sessions}, Turns: {stats['turns']}")
        print(f"    Cache read: ${cr_cost:.2f}  Cache write: ${cw_cost:.2f}  Output: ${out_cost:.2f}  Total: ${total:.2f}")
    
    # What percentage of total cost is "the tax of having a conversation"
    # i.e., cache_read cost, which is just re-reading the same growing context
    print(f"\n--- The 'Conversation Tax' ---")
    total_cr_cost = total_cache_read * 0.50 / 1_000_000
    print(f"  Every turn re-reads the full conversation history from cache.")
    print(f"  Total cache read cost: ${total_cr_cost:.2f}")
    print(f"  With {total_turn_count} turns averaging {total_cache_read/total_turn_count/1000:.0f}K context each,")
    print(f"  you pay ~${total_cache_read/total_turn_count * 0.50 / 1_000_000:.4f} per turn just for the context re-read.")
    print(f"  A 100-turn session costs ~${100 * 80000 * 0.50 / 1_000_000:.2f} in cache reads alone (assuming avg 80K context).")


if __name__ == "__main__":
    analyze()
