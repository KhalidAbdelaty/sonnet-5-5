"""Claude Sonnet 5.5 standard prices per million tokens, from
https://platform.claude.com/docs/en/about-claude/pricing (checked September 30, 2026).
"""
MODEL = "claude-sonnet-5-5"
PRICE_INPUT = 2.00
PRICE_CACHE_WRITE_5M = 2.50
PRICE_CACHE_WRITE_1H = 4.00
PRICE_CACHE_READ = 0.20
PRICE_OUTPUT = 10.00


def call_cost(usage) -> dict:
    """Price one response from its usage object. input_tokens already excludes cached tokens."""
    cc = usage.cache_creation
    w5, w1 = (cc.ephemeral_5m_input_tokens, cc.ephemeral_1h_input_tokens) if cc else (usage.cache_creation_input_tokens, 0)
    thinking = getattr(getattr(usage, "output_tokens_details", None), "thinking_tokens", 0) or 0
    parts = {
        "input_tokens": usage.input_tokens,
        "cache_write_tokens": w5 + w1,
        "cache_read_tokens": usage.cache_read_input_tokens,
        "output_tokens": usage.output_tokens,
        "thinking_tokens": thinking,
        "cost_input": usage.input_tokens * PRICE_INPUT / 1e6,
        "cost_cache_write": (w5 * PRICE_CACHE_WRITE_5M + w1 * PRICE_CACHE_WRITE_1H) / 1e6,
        "cost_cache_read": usage.cache_read_input_tokens * PRICE_CACHE_READ / 1e6,
        "cost_output": usage.output_tokens * PRICE_OUTPUT / 1e6,
    }
    parts["cost"] = parts["cost_input"] + parts["cost_cache_write"] + parts["cost_cache_read"] + parts["cost_output"]
    return parts
