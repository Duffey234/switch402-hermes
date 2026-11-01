"""switch402 routes, inputs and list prices.

Taken from the live discovery documents at https://api.switch402.com/.well-known/x402,
https://api.switch402.com/openapi.json and the MCP server card (Nov 2026). The listed
price is only used to word the approval prompt and to pre-check the spend cap. The
amount that is actually signed comes from the live 402 response for that call, and the
call is refused if it is higher than the price the user approved or higher than the cap.
"""

BASE_URL = "https://api.switch402.com"
API_HOST = "api.switch402.com"

# x402 v2 "exact" on Base mainnet, USDC (6 decimals).
NETWORK = "eip155:8453"
CHAIN_ID = 8453
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_DECIMALS = 6

# tool name -> (path, list price in USDC atomic units, label)
PAID_ROUTES = {
    "s402_scrape": ("/v1/scrape", 8000, "page to markdown"),
    "s402_lite": ("/v1/scrape/lite", 3000, "page to markdown, lite tier"),
    "s402_map": ("/v1/map", 1000, "site map"),
    "s402_metadata": ("/v1/metadata", 1000, "page metadata"),
    "s402_search": ("/v1/search", 8000, "web search"),
    # s402_screenshot picks one of these two routes by its full_page argument.
    "s402_screenshot": ("/v1/screenshot", 8000, "viewport screenshot"),
    "s402_screenshot_full": ("/v1/screenshot/full", 12000, "full-page screenshot"),
}

FREE_QUOTE_PATH = "/v1/quote"


def atomic_to_usd(amount: int) -> float:
    return round(int(amount) / 10 ** USDC_DECIMALS, 6)


def usd_to_atomic(usd: float) -> int:
    # Round down so a cap never authorizes more than the user wrote.
    return int(float(usd) * 10 ** USDC_DECIMALS + 1e-9)


def route_for(tool_name: str, args: dict) -> tuple:
    """(route key, path, list price atomic, label) for a paid tool call."""
    key = tool_name
    if tool_name == "s402_screenshot" and bool((args or {}).get("full_page")):
        key = "s402_screenshot_full"
    path, price, label = PAID_ROUTES[key]
    return key, path, price, label


def request_body(tool_name: str, args: dict) -> dict:
    """The JSON body the live route accepts (see openapi.json)."""
    args = args or {}
    if tool_name == "s402_search":
        body = {"q": str(args.get("q") or "").strip()}
        if args.get("limit") is not None:
            body["limit"] = int(args["limit"])
        return body
    body = {"url": str(args.get("url") or "").strip()}
    if tool_name == "s402_map" and args.get("limit") is not None:
        body["limit"] = int(args["limit"])
    return body
