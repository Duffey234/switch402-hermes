"""Tool schemas (what the model reads). Prices match the live switch402 discovery documents."""

_CAP = {
    "type": "number",
    "minimum": 0,
    "description": "Optional lower spend ceiling in USD for this one call. It can only lower the configured cap, never raise it.",
}
_URL = {"type": "string", "maxLength": 2048, "description": "Public http(s) URL"}

_PAID_NOTE = (" Paid: the user is asked to approve every call before any USDC is spent; "
              "refused in cron and other unattended runs. Use s402_quote first if unsure.")

S402_QUOTE = {
    "name": "s402_quote",
    "description": ("Free. Check switch402's current price for a page fetch, whether payments are on, "
                    "healthy capacity and estimated latency, before using a paid s402_* tool. No wallet needed."),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {**_URL, "description": "Optional page you plan to fetch"},
            "tier": {"type": "string", "enum": ["standard", "lite"], "description": "standard (s402_scrape) or lite (s402_lite)"},
        },
    },
}

S402_SCRAPE = {
    "name": "s402_scrape",
    "description": "Fetch a public web page as clean markdown (title + markdown). $0.008 USDC on Base per page, charged only on success." + _PAID_NOTE,
    "parameters": {"type": "object", "properties": {"url": {**_URL, "description": "Public http(s) page to convert to markdown"}, "max_spend_usd": _CAP}, "required": ["url"]},
}

S402_LITE = {
    "name": "s402_lite",
    "description": "Cheaper page-to-markdown fetch (lite tier), good for simple static pages. $0.003 USDC on Base per page, charged only on success." + _PAID_NOTE,
    "parameters": {"type": "object", "properties": {"url": {**_URL, "description": "Public http(s) page to convert to markdown"}, "max_spend_usd": _CAP}, "required": ["url"]},
}

S402_SCREENSHOT = {
    "name": "s402_screenshot",
    "description": ("Screenshot a public page (JavaScript rendered) as a PNG saved to a local file. Viewport 1280x800 "
                    "$0.008, or full_page=true for the whole scrollable page $0.012, USDC on Base, charged only on success." + _PAID_NOTE),
    "parameters": {"type": "object", "properties": {
        "url": {**_URL, "description": "Public http(s) page to capture"},
        "full_page": {"type": "boolean", "description": "Capture the whole scrollable page ($0.012) instead of the viewport ($0.008)"},
        "max_spend_usd": _CAP}, "required": ["url"]},
}

S402_SEARCH = {
    "name": "s402_search",
    "description": "Web search: up to 10 ranked results (title, URL, snippet). $0.008 USDC on Base per query, charged only on success." + _PAID_NOTE,
    "parameters": {"type": "object", "properties": {
        "q": {"type": "string", "minLength": 1, "maxLength": 400, "description": "Search query (1-400 characters)"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Max results (default 10)"},
        "max_spend_usd": _CAP}, "required": ["q"]},
}

S402_MAP = {
    "name": "s402_map",
    "description": "Site map: same-site links on a page plus sitemap URLs, deduped. $0.001 USDC on Base per call, charged only on success." + _PAID_NOTE,
    "parameters": {"type": "object", "properties": {
        "url": {**_URL, "description": "Public http(s) page or site root"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Max URLs returned (default 200)"},
        "max_spend_usd": _CAP}, "required": ["url"]},
}

S402_METADATA = {
    "name": "s402_metadata",
    "description": "Page metadata: title, meta description, canonical, lang, OpenGraph and Twitter tags, JSON-LD. $0.001 USDC on Base per call, charged only on success." + _PAID_NOTE,
    "parameters": {"type": "object", "properties": {"url": {**_URL, "description": "Public http(s) HTML page"}, "max_spend_usd": _CAP}, "required": ["url"]},
}
