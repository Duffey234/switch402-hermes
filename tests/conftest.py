import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import client as client_mod  # noqa: E402
import gate as gate_mod  # noqa: E402
import handlers  # noqa: E402
import routes  # noqa: E402

# A throwaway test key (well-known Hardhat/Anvil account #0). Not a real wallet.
TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
DUMMY_PAY_TO = "0x000000000000000000000000000000000000dEaD"


def make_402(path: str, amount: int, *, network=routes.NETWORK, asset=routes.USDC_BASE, host=routes.API_HOST):
    url = f"https://{host}{path}"
    pr = {
        "x402Version": 2,
        "error": "Payment required",
        "resource": {"url": url, "description": "test", "mimeType": "application/json"},
        "accepts": [{
            "scheme": "exact", "network": network, "amount": str(amount), "asset": asset,
            "payTo": DUMMY_PAY_TO, "maxTimeoutSeconds": 60, "extra": {"name": "USD Coin", "version": "2"},
            "resource": url,
        }],
    }
    hdr = base64.b64encode(json.dumps(pr).encode()).decode()
    return client_mod.HttpResponse(402, {"payment-required": hdr, "content-type": "application/json"},
                                   json.dumps(pr).encode())


class FakeTransport:
    """Mocked HTTP: unpaid POST -> 402, paid POST -> 200 with a settlement header."""

    def __init__(self, *, price_override=None, paid_body=None, quote_body=None, bad_terms=None):
        self.calls = []
        self.price_override = price_override or {}
        self.paid_body = paid_body
        self.quote_body = quote_body or {"endpoint": "/v1/scrape", "price": "$0.008 USDC on Base", "payments": "on", "available": True}
        self.bad_terms = bad_terms or {}

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        path = url.split(routes.API_HOST, 1)[1].split("?", 1)[0]
        if method == "GET" and path == routes.FREE_QUOTE_PATH:
            return client_mod.HttpResponse(200, {}, json.dumps(self.quote_body).encode())
        listed = {p: a for (p, a, _l) in routes.PAID_ROUTES.values()}
        if method == "POST" and path in listed:
            if "PAYMENT-SIGNATURE" not in headers:
                return make_402(path, self.price_override.get(path, listed[path]), **self.bad_terms)
            settle = base64.b64encode(json.dumps({"success": True, "transaction": "0xabc", "network": routes.NETWORK}).encode()).decode()
            body_out = self.paid_body if self.paid_body is not None else {"url": "https://example.com/", "markdown": "# Example"}
            return client_mod.HttpResponse(200, {"payment-response": settle}, json.dumps(body_out).encode())
        return client_mod.HttpResponse(404, {}, b"{}")

    def paid_posts(self):
        return [c for c in self.calls if "PAYMENT-SIGNATURE" in c["headers"]]


class Env:
    def __init__(self, tmp_path, *, key=TEST_KEY, cap=0.02, cron=False, transport=None):
        self.key = key
        self.cap = cap
        self.cron = cron
        self.transport = transport or FakeTransport()
        self.client = client_mod.Switch402Client(transport=self.transport, key_source=lambda: self.key)
        self.gate = gate_mod.SpendGate(cap_usd=lambda: self.cap, in_cron=lambda: self.cron,
                                       has_wallet=self.client.has_wallet)
        handlers.set_runtime(handlers.Runtime(self.client, self.gate, data_dir=lambda: tmp_path))

    def approve(self, tool, args, choice="once", task_id="t1", **extra):
        """Simulate Hermes: pre_tool_call -> human prompt -> post_approval_response."""
        d = self.gate.pre_tool_call(tool_name=tool, args=args, task_id=task_id)
        if d and d.get("action") == "approve":
            self.gate.post_approval_response(choice=choice, pattern_key=f"plugin_rule:{d['rule_key']}",
                                             pattern_keys=[f"plugin_rule:{d['rule_key']}"],
                                             description=d["message"], **extra)
        return d


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def load_plugin_module():
    spec = importlib.util.spec_from_file_location("switch402_plugin", ROOT / "__init__.py",
                                                  submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["switch402_plugin"] = module
    spec.loader.exec_module(module)
    return module
