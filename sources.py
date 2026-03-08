"""Multi-source session discovery for Claude Code usage analysis.

Each source points to a Claude projects directory — local or via SSHFS mount.
Sources with requires_mount are skipped with a warning if the mount is unavailable.

Source configuration:
- An implicit "local" source (~/.claude/projects) is always present
- Extra sources can be defined in ~/.config/claude-usage/sources.json
- Run `claude_usage sources init` to scaffold a sample config file

Config file format (sources.json):
[
    {
        "name": "my-server",
        "path": "/mnt/remote/.claude/projects",
        "type": "sshfs",
        "requires_mount": "my-server"
    }
]
"""

import json
import sys
from pathlib import Path

from .loader import discover_session_files


CONFIG_DIR = Path.home() / ".config" / "claude-usage"
CONFIG_FILE = CONFIG_DIR / "sources.json"

IMPLICIT_LOCAL = {
    "name": "local",
    "path": "~/.claude/projects",
    "type": "local",
}

SAMPLE_CONFIG = [
    {
        "name": "remote-server",
        "path": "/mnt/box/remote-host/root/.claude/projects",
        "type": "sshfs",
        "requires_mount": "remote-host",
    },
]


def _load_config() -> list[dict]:
    """Load extra sources from config file, if it exists."""
    if not CONFIG_FILE.exists():
        return []
    try:
        data = json.loads(CONFIG_FILE.read_text())
        if not isinstance(data, list):
            print(f"  Warning: {CONFIG_FILE} should be a JSON array, ignoring", file=sys.stderr)
            return []
        for entry in data:
            if "name" not in entry or "path" not in entry:
                print(f"  Warning: source entry missing 'name' or 'path', skipping: {entry}", file=sys.stderr)
                data.remove(entry)
        return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"  Warning: failed to read {CONFIG_FILE}: {e}", file=sys.stderr)
        return []


def _get_sources() -> list[dict]:
    """Return implicit local source + any configured extra sources."""
    return [IMPLICIT_LOCAL] + _load_config()


def init_config() -> str:
    """Create a sample sources.json config file. Returns status message."""
    if CONFIG_FILE.exists():
        return f"Config already exists: {CONFIG_FILE}"
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(SAMPLE_CONFIG, indent=2) + "\n")
    return f"Created sample config: {CONFIG_FILE}\nEdit it to add your remote sources."


def _is_mount_available(source: dict) -> bool:
    """Check if an SSHFS source is mounted and accessible."""
    path = Path(source["path"]).expanduser()
    if not path.exists():
        return False
    # SSHFS mounts that went stale show as empty dirs
    try:
        next(path.iterdir())
        return True
    except (StopIteration, OSError):
        return False


def get_source(name: str) -> dict | None:
    """Look up a source by name."""
    for s in _get_sources():
        if s["name"] == name:
            return s
    return None


def list_sources() -> list[dict]:
    """Return all sources with availability status."""
    result = []
    for s in _get_sources():
        available = True
        if s.get("requires_mount"):
            available = _is_mount_available(s)
        result.append({**s, "available": available})
    return result


def discover_all_sources(
    hours: int | None = None,
    date: str | None = None,
    source_name: str | None = None,
    include_subagents: bool = False,
) -> list[tuple[str, Path]]:
    """Discover session files across all (or one) source.

    Returns list of (source_name, file_path) tuples.
    """
    all_sources = _get_sources()
    sources_to_scan = all_sources
    if source_name:
        src = get_source(source_name)
        if not src:
            print(f"  Unknown source: {source_name}", file=sys.stderr)
            print(f"  Available: {', '.join(s['name'] for s in all_sources)}", file=sys.stderr)
            return []
        sources_to_scan = [src]

    results = []
    for src in sources_to_scan:
        if src.get("requires_mount") and not _is_mount_available(src):
            print(f"  Skipping {src['name']} (not mounted)", file=sys.stderr)
            continue

        path = str(Path(src["path"]).expanduser())
        files = discover_session_files(
            hours=hours, date=date, path=path,
            include_subagents=include_subagents,
        )
        for f in files:
            results.append((src["name"], f))

    return results
