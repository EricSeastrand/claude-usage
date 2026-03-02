"""Analyze what causes the biggest context jumps."""

import json
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude" / "projects"

def find_biggest_jump_sessions(top_n=4):
    """Find the sessions with the largest single-turn context jumps."""
    all_files = list(CLAUDE_DIR.glob("*/*.jsonl"))
    session_jumps = []

    for path in all_files:
        prev_context = None
        max_jump = 0

        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                usage = rec.get("message", {}).get("usage")
                if not usage:
                    continue
                ctx = (
                    usage.get("input_tokens", 0)
                    + usage.get("cache_creation", {}).get("ephemeral_5m_input_tokens", 0)
                    + usage.get("cache_creation", {}).get("ephemeral_1h_input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0)
                )
                if ctx == 0:
                    continue
                if prev_context is not None:
                    jump = ctx - prev_context
                    if jump > max_jump:
                        max_jump = jump
                prev_context = ctx

        if max_jump > 0:
            session_jumps.append((path.stem[:8], max_jump))

    session_jumps.sort(key=lambda x: x[1], reverse=True)
    return [sid for sid, _ in session_jumps[:top_n]]


def analyze_session_trajectory(session_prefix):
    """Look at how context grows turn by turn, and what content arrives at big jumps."""
    all_files = list(CLAUDE_DIR.glob("*/*.jsonl"))
    
    target_file = None
    for f in all_files:
        if f.stem.startswith(session_prefix):
            target_file = f
            break
    
    if not target_file:
        print(f"  Session {session_prefix} not found")
        return
    
    turns = []  # (turn_idx, context_size, type, brief_content)
    turn_idx = 0
    
    with open(target_file) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            
            rec_type = rec.get("type")
            
            if rec_type == "assistant":
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
                
                # Get brief content of what the assistant said/did
                content = msg.get("content", [])
                brief = ""
                tool_uses = []
                for block in (content if isinstance(content, list) else []):
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text = block.get("text", "")[:80]
                            if text:
                                brief = text
                        elif block.get("type") == "tool_use":
                            tool_name = block.get("name", "?")
                            tool_input = json.dumps(block.get("input", {}))[:60]
                            tool_uses.append(f"{tool_name}({tool_input})")
                
                summary = brief or " | ".join(tool_uses[:3]) or "(empty)"
                
                turns.append({
                    "idx": turn_idx,
                    "context": context_size,
                    "input": input_tok,
                    "cache_read": cache_read,
                    "cache_write": cache_write_5m + cache_write_1h,
                    "output": output_tok,
                    "summary": summary[:100],
                })
                turn_idx += 1
    
    # Now find the biggest jumps
    print(f"\n{'='*70}")
    print(f"Session: {session_prefix}  ({len(turns)} turns)")
    print(f"{'='*70}")
    
    # Show trajectory at key points
    print(f"\n  Context trajectory (sampled):")
    
    # Find biggest jumps
    jumps = []
    for i in range(1, len(turns)):
        jump = turns[i]["context"] - turns[i-1]["context"]
        jumps.append((i, jump))
    
    jumps.sort(key=lambda x: x[1], reverse=True)
    
    # Show top 5 jumps with context
    print(f"\n  Top 5 context jumps:")
    for idx, jump_size in jumps[:5]:
        t = turns[idx]
        prev = turns[idx-1]
        print(f"    Turn {idx}: {prev['context']//1000}K → {t['context']//1000}K  (+{jump_size//1000}K)")
        print(f"      in={t['input']}  cacheR={t['cache_read']//1000}K  cacheW={t['cache_write']//1000}K  out={t['output']}")
        print(f"      {t['summary']}")
    
    # Show the drops too (context compression events)
    drops = [(i, j) for i, j in jumps if j < -10000]
    drops.sort(key=lambda x: x[1])
    if drops:
        print(f"\n  Context compressions (drops >10K):")
        for idx, drop_size in drops[:5]:
            t = turns[idx]
            prev = turns[idx-1]
            print(f"    Turn {idx}: {prev['context']//1000}K → {t['context']//1000}K  ({drop_size//1000}K)")
    
    # Show every 50th turn for trajectory
    print(f"\n  Full trajectory (every 50 turns):")
    for t in turns[::50]:
        bar = "█" * (t["context"] // 5000)
        print(f"    Turn {t['idx']:4d}: {t['context']//1000:>4}K  {bar}")
    if turns:
        t = turns[-1]
        bar = "█" * (t["context"] // 5000)
        print(f"    Turn {t['idx']:4d}: {t['context']//1000:>4}K  {bar} (last)")


import sys

if len(sys.argv) > 1:
    targets = sys.argv[1:]
else:
    print("Auto-detecting sessions with biggest context jumps...")
    targets = find_biggest_jump_sessions()
    print(f"Found: {', '.join(targets)}")

for sid in targets:
    analyze_session_trajectory(sid)
