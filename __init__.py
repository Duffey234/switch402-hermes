"""switch402 Hermes plugin: registration entry point (public plugin APIs only)."""

try:
    from . import handlers, schemas
    from .client import Switch402Client, wallet_key
    from .gate import SpendGate
except ImportError:  # loaded as a top-level module
    import handlers, schemas  # noqa: E401
    from client import Switch402Client, wallet_key
    from gate import SpendGate

DEFAULT_MAX_SPEND_USD = 0.02

_TOOLS = [
    ("s402_quote", schemas.S402_QUOTE, handlers.s402_quote, "💵"),
    ("s402_scrape", schemas.S402_SCRAPE, handlers.s402_scrape, "📄"),
    ("s402_lite", schemas.S402_LITE, handlers.s402_lite, "📃"),
    ("s402_screenshot", schemas.S402_SCREENSHOT, handlers.s402_screenshot, "📸"),
    ("s402_search", schemas.S402_SEARCH, handlers.s402_search, "🔎"),
    ("s402_map", schemas.S402_MAP, handlers.s402_map, "🗺️"),
    ("s402_metadata", schemas.S402_METADATA, handlers.s402_metadata, "🏷️"),
]


def _cfg(ctx, key, default):
    try:
        value = ctx.get_config(key, default=default)
    except Exception:
        return default
    return default if value in (None, "") else value


def _cap_reader(ctx):
    def cap() -> float:
        try:
            value = float(_cfg(ctx, "max_spend_usd", DEFAULT_MAX_SPEND_USD))
        except (TypeError, ValueError):
            value = DEFAULT_MAX_SPEND_USD
        return max(0.0, value)
    return cap


def _cron_reader(ctx):
    def in_cron() -> bool:
        probe = getattr(ctx, "current_cron_execution", None)
        if probe is None:
            return False
        try:
            return probe() is not None
        except Exception:
            return True  # cannot tell: fail safe, treat as unattended
    return in_cron


def register(ctx) -> None:
    try:
        timeout = int(_cfg(ctx, "timeout_seconds", 60))
    except (TypeError, ValueError):
        timeout = 60
    client = Switch402Client(timeout=timeout, key_source=wallet_key)
    gate = SpendGate(cap_usd=_cap_reader(ctx), in_cron=_cron_reader(ctx), has_wallet=client.has_wallet)
    handlers.set_runtime(handlers.Runtime(client, gate))
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(name=name, toolset="switch402", schema=schema, handler=handler,
                          description=schema["description"], emoji=emoji)
    ctx.register_hook("pre_tool_call", gate.pre_tool_call)
    ctx.register_hook("post_approval_response", gate.post_approval_response)
