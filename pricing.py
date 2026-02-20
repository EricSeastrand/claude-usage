"""Anthropic Claude pricing tables ($ per million tokens).
Rates from https://www.anthropic.com/pricing as of 2025-05.
Databricks routes through Anthropic at $0.07/DBU — effective per-token
rates are identical to direct Anthropic pricing.
Cache pricing tiers:
- 5-minute (ephemeral): cache_write costs 25% above base input (1.25x)
- 1-hour (extended):    cache_write costs 100% above base input (2x)
- cache_read:           90% discount from base input (0.1x)
"""
# model pattern -> {input, output, cache_write_5m, cache_write_1h, cache_read} $/MTok
PRICING = {
    "opus-4-5": {
        "input": 5.0,
        "output": 25.0,
        "cache_write_5m": 6.25,
        "cache_write_1h": 10.0,
        "cache_read": 0.50,
    },
    "opus-4-6": {
        "input": 5.0,
        "output": 25.0,
        "cache_write_5m": 6.25,
        "cache_write_1h": 10.0,
        "cache_read": 0.50,
    },
    "opus-4-0": {
        "input": 15.0,
        "output": 75.0,
        "cache_write_5m": 18.75,
        "cache_write_1h": 30.0,
        "cache_read": 1.50,
    },
    "opus-4-1": {
        "input": 15.0,
        "output": 75.0,
        "cache_write_5m": 18.75,
        "cache_write_1h": 30.0,
        "cache_read": 1.50,
    },
    "sonnet-4-5": {
        "input": 3.0,
        "output": 15.0,
        "cache_write_5m": 3.75,
        "cache_write_1h": 6.0,
        "cache_read": 0.30,
    },
    "sonnet-4-6": {
        "input": 3.0,
        "output": 15.0,
        "cache_write_5m": 3.75,
        "cache_write_1h": 6.0,
        "cache_read": 0.30,
    },
    "sonnet-4-0": {
        "input": 3.0,
        "output": 15.0,
        "cache_write_5m": 3.75,
        "cache_write_1h": 6.0,
        "cache_read": 0.30,
    },
    "sonnet-4-1": {
        "input": 3.0,
        "output": 15.0,
        "cache_write_5m": 3.75,
        "cache_write_1h": 6.0,
        "cache_read": 0.30,
    },
    "sonnet-3-7": {
        "input": 3.0,
        "output": 15.0,
        "cache_write_5m": 3.75,
        "cache_write_1h": 6.0,
        "cache_read": 0.30,
    },
    "haiku-4-5": {
        "input": 1.0,
        "output": 5.0,
        "cache_write_5m": 1.25,
        "cache_write_1h": 2.0,
        "cache_read": 0.10,
    },
    "haiku-3-5": {
        "input": 0.80,
        "output": 4.0,
        "cache_write_5m": 1.0,
        "cache_write_1h": 1.60,
        "cache_read": 0.08,
    },
}
# Fallback for unknown models — use Sonnet pricing as a reasonable middle ground
_FALLBACK = PRICING["sonnet-4-5"]


def get_pricing(model_id: str) -> dict:
    """Match a model ID string (e.g. 'claude-opus-4-6') to pricing rates.
    Strips common prefixes and matches against known model patterns.
    Returns fallback pricing for unrecognized models.
    """
    normalized = model_id.lower()
    # Strip common prefixes
    for prefix in ("claude-", "anthropic-", "databricks-"):
        normalized = normalized.removeprefix(prefix)
    for pattern, rates in PRICING.items():
        if pattern in normalized:
            return rates
    return _FALLBACK


def compute_cost(
    input_tokens: int,
    output_tokens: int,
    cache_write_5m_tokens: int,
    cache_write_1h_tokens: int,
    cache_read_tokens: int,
    rates: dict,
) -> float:
    """Compute cost in dollars from token counts and pricing rates."""
    return (
        input_tokens * rates["input"]
        + output_tokens * rates["output"]
        + cache_write_5m_tokens * rates["cache_write_5m"]
        + cache_write_1h_tokens * rates["cache_write_1h"]
        + cache_read_tokens * rates["cache_read"]
    ) / 1_000_000