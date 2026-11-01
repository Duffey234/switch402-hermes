# switch402 for Hermes Agent

Web data tools for [Hermes Agent](https://github.com/NousResearch/hermes-agent): turn any public page into
clean markdown, map a site, read page metadata, take screenshots and search the web. Each paid call is a
single x402 payment in USDC on Base, made from your own wallet after you approve it.

Built and maintained by the switch402 team (the people who run api.switch402.com).

## Disclosure

Read this before installing.

- **Network calls.** Every tool sends its input (the URL, or your search query) to **api.switch402.com**,
  which we operate. Nothing else is contacted. switch402 fetches the page or runs the search for you and
  returns the result. Its quote output notes that a request may be served by a third-party provider
  or by a service we operate. The plugin sends no telemetry.
- **Spending.** The paid tools spend **USDC on Base** from the wallet whose private key you put in
  `SWITCH402_WALLET_KEY`. Each paid call signs one USDC transfer authorization for that call's price
  (from $0.001 to $0.012, listed below), and the service charges only when the call succeeds.
- **Approval for every paid call.** Before any paid call, Hermes shows you the approval prompt with the
  price, the route and the per-request cap. The plugin pays only after you answer that prompt yourself.
  "Session" and "always" answers count for that one call only, so every paid call asks again.
- **Spend cap.** `max_spend_usd` (default $0.02) caps each request. A call priced above the cap is refused
  before you see a prompt. If the live price comes back higher than the price you approved, or than
  the cap, the plugin refuses and signs nothing.
- **Unattended runs.** Payment is off in cron and scheduled runs. It is also off wherever no human answers
  the prompt: yolo mode, `approvals.mode: off`, smart (LLM) approvals, auto-approve settings for cron or
  single-query runs, and gateways with no one to answer. In all those cases the paid tools fail safe and
  do nothing.
- **Credentials.** `SWITCH402_WALLET_KEY` is read through Hermes' profile-scoped secret store, used only on
  your machine to sign, and never sent, logged or included in tool output. Use a separate wallet that
  holds only a small amount of USDC.
- **Local files.** `s402_screenshot` saves the PNG under Hermes' per-plugin data directory
  (`<HERMES_HOME>/plugin-data/switch402/screenshots/`) and returns the file path. The plugin writes
  nothing else to disk.
- **No self-update.** The plugin never downloads or replaces its own code. Updates arrive only through the
  Hermes plugin catalog pin.

## Tools and prices

| Tool | What it does | Price per call (USDC on Base) |
|---|---|---|
| `s402_quote` | Current price, payment status, healthy capacity and estimated latency | Free |
| `s402_scrape` | Public page to clean markdown | $0.008 |
| `s402_lite` | Page to markdown, lite tier, for simple pages | $0.003 |
| `s402_screenshot` | Screenshot as PNG, viewport 1280x800 (`full_page: true` for the whole page) | $0.008 ($0.012 full page) |
| `s402_search` | Web search, up to 10 ranked results | $0.008 |
| `s402_map` | Same-site links plus sitemap URLs | $0.001 |
| `s402_metadata` | Title, description, canonical, OpenGraph/Twitter, JSON-LD | $0.001 |

You are charged only when a call succeeds. The prices above come from switch402's public discovery
documents (`https://api.switch402.com/.well-known/x402`, `https://api.switch402.com/openapi.json`). The
amount actually signed always comes from that call's live 402 response and can never be more than you
approved.

## Install

```bash
hermes plugins install switch402
```

Set the wallet key when prompted, or later in your Hermes `.env`:

```
SWITCH402_WALLET_KEY=0x...   # a Base wallet with a little USDC
```

`s402_quote` works without a key. Optional settings (`config.yaml`):

```yaml
plugins:
  entries:
    switch402:
      settings:
        max_spend_usd: 0.02     # per-request ceiling
        timeout_seconds: 60
```

The model can also pass `max_spend_usd` on a single call, but only to lower the cap, never to raise it.

## How a paid call works

1. The model calls, for example, `s402_scrape` with a URL.
2. The plugin's `pre_tool_call` hook checks the cap and the run type, then asks Hermes to show you the approval
   prompt, for example "Pay $0.008 USDC on Base to switch402 for page to markdown (/v1/scrape) of
   'https://example.com/'? Cap $0.02; charged only on success."
3. You approve. The plugin requests the route, reads the live x402 terms, checks the network, the asset,
   the host and the amount, signs one authorization for exactly that amount, and sends the request again
   with the payment.
4. The result comes back with a `payment` field (amount, network, settlement transaction).

## Hermes surfaces used

`ctx.register_tool`, `ctx.register_hook("pre_tool_call")` (block or approve directives),
`ctx.register_hook("post_approval_response")` (records that a human answered), `ctx.get_config`,
`ctx.current_cron_execution()`, `agent.secret_scope.get_secret_str` and `plugins.plugin_storage.plugin_data_dir`.
No core code is patched.

## Development

```bash
pip install -e '.[test]'
pytest -q
hermes plugins validate . --install-deps
```

The tests mock all HTTP and use a throwaway test key, so they make no network calls and spend nothing.

## License

MIT
