import json
import logging
from pathlib import Path

import yaml_free_manifest  # noqa: F401  (see tests/yaml_free_manifest.py)

from conftest import TEST_ADDR, TEST_KEY, Env, FakeTransport, load_plugin_module
import client as client_mod
import handlers
import routes

PAID = ["s402_scrape", "s402_lite", "s402_screenshot", "s402_search", "s402_map", "s402_metadata"]


def call(tool, args, **kw):
    return json.loads(getattr(handlers, tool)(args, task_id=kw.get("task_id", "t1")))


def args_for(tool):
    return {"q": "x402 payment protocol"} if tool == "s402_search" else {"url": "https://example.com/"}


# -- registration -----------------------------------------------------------------------------
def test_registers_declared_tools_and_hooks():
    mod = load_plugin_module()

    class Ctx:
        def __init__(self):
            self.tools, self.hooks = [], []

        def register_tool(self, **kw):
            self.tools.append(kw)

        def register_hook(self, name, cb, **kw):
            self.hooks.append((name, cb))

        def get_config(self, key, default=None):
            return default

        def current_cron_execution(self):
            return None

    ctx = Ctx()
    mod.register(ctx)
    manifest = yaml_free_manifest.load(Path(__file__).parents[1] / "plugin.yaml")
    assert {t["name"] for t in ctx.tools} == set(manifest["provides_tools"])
    assert [h for h, _ in ctx.hooks] == manifest["provides_hooks"]
    assert all(t["toolset"] == "switch402" and callable(t["handler"]) for t in ctx.tools)
    assert all({"name", "description", "parameters"} <= set(t["schema"]) for t in ctx.tools)


# -- free quote -------------------------------------------------------------------------------
def test_free_quote_works_without_wallet(tmp_path):
    env = Env(tmp_path, key="")
    out = call("s402_quote", {"url": "https://example.com/", "tier": "lite"})
    assert out["ok"] is True
    assert out["quote"]["payments"] == "on"
    assert out["list_prices_usd"]["s402_lite"] == "$0.003"
    (c,) = env.transport.calls
    assert c["method"] == "GET" and "tier=lite" in c["url"] and "PAYMENT-SIGNATURE" not in c["headers"]
    assert env.gate.pre_tool_call(tool_name="s402_quote", args={}) is None  # never gated


# -- approval required ------------------------------------------------------------------------
def test_paid_call_without_approval_is_refused(env):
    for tool in PAID:
        out = call(tool, args_for(tool))
        assert out["ok"] is False and out["error"]["code"] == "not_approved"
    assert env.transport.calls == []  # nothing probed, nothing signed


def test_hook_escalates_every_paid_tool_to_the_approval_gate(env):
    for tool in PAID:
        d = env.gate.pre_tool_call(tool_name=tool, args=args_for(tool), task_id="t1")
        assert d["action"] == "approve"
        assert d["rule_key"].startswith("switch402:pay:")
        assert "USDC on Base" in d["message"] and "$" in d["message"]


def test_human_once_approval_pays_exactly_once(env):
    a = {"url": "https://example.com/"}
    env.approve("s402_scrape", a, choice="once")
    out = call("s402_scrape", a)
    assert out["ok"] is True, out
    assert out["payment"]["amount_usd"] == 0.008 and out["payment"]["transaction"] == "0xabc"
    (paid,) = env.transport.paid_posts()
    sig = json.loads(__import__("base64").b64decode(paid["headers"]["PAYMENT-SIGNATURE"]))
    assert sig["x402Version"] == 2 and sig["accepted"]["amount"] == "8000"
    assert sig["payload"]["authorization"]["from"] == TEST_ADDR
    # the approval is one-shot
    again = call("s402_scrape", a)
    assert again["error"]["code"] == "not_approved"
    assert len(env.transport.paid_posts()) == 1


def test_signature_recovers_to_the_wallet(env):
    from eth_account import Account
    from eth_account.messages import encode_typed_data

    a = {"url": "https://example.com/"}
    env.approve("s402_lite", a)
    assert call("s402_lite", a)["ok"] is True
    (paid,) = env.transport.paid_posts()
    sig = json.loads(__import__("base64").b64decode(paid["headers"]["PAYMENT-SIGNATURE"]))
    auth = sig["payload"]["authorization"]
    msg = encode_typed_data(
        domain_data={"name": "USD Coin", "version": "2", "chainId": 8453, "verifyingContract": routes.USDC_BASE},
        message_types={"TransferWithAuthorization": [
            {"name": "from", "type": "address"}, {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"}]},
        message_data={"from": auth["from"], "to": auth["to"], "value": int(auth["value"]),
                      "validAfter": int(auth["validAfter"]), "validBefore": int(auth["validBefore"]),
                      "nonce": bytes.fromhex(auth["nonce"][2:])})
    assert Account.recover_message(msg, signature=sig["payload"]["signature"]) == TEST_ADDR
    assert auth["value"] == "3000"


def test_denied_timeout_cancelled_and_smart_approvals_do_not_pay(env):
    a = {"url": "https://example.com/"}
    for choice in ("deny", "timeout", "cancelled", "notify_failed", "smart_approve"):
        env.approve("s402_scrape", a, choice=choice)
        assert call("s402_scrape", a)["error"]["code"] == "not_approved", choice
    # a "once" answered by an LLM (decided_by set) is not a human answer
    env.approve("s402_scrape", a, choice="once", decided_by="aux_llm")
    assert call("s402_scrape", a)["error"]["code"] == "not_approved"
    assert env.transport.paid_posts() == []


def test_session_or_always_answers_do_not_preapprove_later_calls(env):
    a = {"url": "https://example.com/"}
    env.approve("s402_map", a, choice="always")
    assert call("s402_map", a)["ok"] is True
    # Next call: Hermes skips the prompt (no post_approval_response), so no human answer exists.
    d = env.gate.pre_tool_call(tool_name="s402_map", args=a, task_id="t1")
    assert d["action"] == "approve"
    assert call("s402_map", a)["error"]["code"] == "not_approved"
    assert len(env.transport.paid_posts()) == 1


def test_approval_is_bound_to_the_exact_arguments(env):
    env.approve("s402_scrape", {"url": "https://example.com/"})
    out = call("s402_scrape", {"url": "https://other.example/"})
    assert out["error"]["code"] == "not_approved"
    assert env.transport.paid_posts() == []


# -- cap --------------------------------------------------------------------------------------
def test_cap_blocks_before_the_prompt(tmp_path):
    env = Env(tmp_path, cap=0.005)
    d = env.gate.pre_tool_call(tool_name="s402_scrape", args={"url": "https://example.com/"}, task_id="t1")
    assert d["action"] == "block" and "cap" in d["message"]
    # cheaper routes still go to the prompt
    assert env.gate.pre_tool_call(tool_name="s402_lite", args={"url": "https://example.com/"})["action"] == "approve"
    # full-page screenshot ($0.012) passes the default $0.02 cap but not a $0.01 cap
    env.cap = 0.01
    full = env.gate.pre_tool_call(tool_name="s402_screenshot", args={"url": "https://example.com/", "full_page": True})
    assert full["action"] == "block"
    assert env.gate.pre_tool_call(tool_name="s402_screenshot", args={"url": "https://example.com/"})["action"] == "approve"


def test_per_call_max_spend_can_only_lower_the_cap(env):
    lower = env.gate.pre_tool_call(tool_name="s402_search", args={"q": "a", "max_spend_usd": 0.001})
    assert lower["action"] == "block"
    higher = env.gate.pre_tool_call(tool_name="s402_screenshot", args={"url": "https://e.com/", "full_page": True, "max_spend_usd": 5})
    assert higher["action"] == "approve" and "Cap $0.02" in higher["message"]


def test_live_price_above_approved_price_is_refused_without_signing(tmp_path):
    t = FakeTransport(price_override={"/v1/scrape": 9000})
    env = Env(tmp_path, transport=t)
    a = {"url": "https://example.com/"}
    env.approve("s402_scrape", a)
    out = call("s402_scrape", a)
    assert out["ok"] is False and out["error"]["code"] == "over_limit"
    assert t.paid_posts() == [] and len(t.calls) == 1


def test_cap_lowered_after_approval_still_applies(env):
    a = {"url": "https://example.com/"}
    env.approve("s402_scrape", a)
    env.cap = 0.001
    out = call("s402_scrape", a)
    assert out["error"]["code"] == "over_limit"
    assert env.transport.paid_posts() == []


def test_wrong_terms_are_never_signed(tmp_path):
    for bad in ({"network": "eip155:1"}, {"asset": "0x0000000000000000000000000000000000000001"}, {"host": "evil.example"}):
        t = FakeTransport(bad_terms=bad)
        env = Env(tmp_path, transport=t)
        a = {"url": "https://example.com/"}
        env.approve("s402_scrape", a)
        out = call("s402_scrape", a)
        assert out["error"]["code"] == "bad_terms", bad
        assert t.paid_posts() == []


# -- unattended -------------------------------------------------------------------------------
def test_cron_runs_are_blocked_in_the_hook_and_the_handler(tmp_path):
    env = Env(tmp_path)
    a = {"url": "https://example.com/"}
    env.approve("s402_scrape", a)  # approved interactively...
    env.cron = True  # ...but the call executes inside a cron run
    d = env.gate.pre_tool_call(tool_name="s402_scrape", args=a)
    assert d["action"] == "block" and "cron" in d["message"]
    assert call("s402_scrape", a)["error"]["code"] == "not_approved"
    assert env.transport.calls == []


def test_unattended_auto_approve_without_a_human_answer_is_refused(env):
    # yolo / approvals off / single-query or cron approve modes: Hermes lets the call through
    # without ever prompting, so post_approval_response never fires.
    a = {"url": "https://example.com/"}
    for tool in PAID:
        assert env.gate.pre_tool_call(tool_name=tool, args=args_for(tool), task_id="t1")["action"] == "approve"
        assert call(tool, args_for(tool))["error"]["code"] == "not_approved"
    assert env.transport.calls == []


def test_missing_wallet_blocks_paid_tools(tmp_path):
    env = Env(tmp_path, key="")
    d = env.gate.pre_tool_call(tool_name="s402_scrape", args={"url": "https://example.com/"})
    assert d["action"] == "block" and "SWITCH402_WALLET_KEY" in d["message"]


# -- secrets ----------------------------------------------------------------------------------
def test_key_never_appears_in_output_logs_or_errors(tmp_path, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    env = Env(tmp_path)
    outputs = []
    for tool in PAID:
        env.approve(tool, args_for(tool))
        outputs.append(getattr(handlers, tool)(args_for(tool), task_id="t1"))
        outputs.append(json.dumps(env.gate.pre_tool_call(tool_name=tool, args=args_for(tool))))
    # a broken key: the error must not echo it
    bad_key = "0x" + "zz" * 32
    env.key = bad_key
    env.approve("s402_scrape", {"url": "https://example.com/"})
    bad = handlers.s402_scrape({"url": "https://example.com/"}, task_id="t1")
    assert json.loads(bad)["error"]["code"] == "sign_failed"
    outputs.append(bad)
    captured = capsys.readouterr()
    blob = "\n".join(outputs) + captured.out + captured.err + caplog.text
    sent = "\n".join(json.dumps({k: v for k, v in c.items() if k != "body"}) + (c["body"] or b"").decode()
                     for c in env.transport.calls)
    for secret in (TEST_KEY, TEST_KEY[2:], bad_key, bad_key[2:]):
        assert secret not in blob
        assert secret not in sent  # never sent over HTTP either
    assert TEST_KEY not in repr(env.client.__dict__.get("_key_source"))


# -- screenshot -------------------------------------------------------------------------------
def test_screenshot_is_saved_to_a_file_not_returned_inline(tmp_path):
    import base64
    png = b"\x89PNG\r\n\x1a\nfake"
    t = FakeTransport(paid_body={"url": "https://example.com/", "content_type": "image/png",
                                 "image_base64": base64.b64encode(png).decode(), "full_page": True})
    env = Env(tmp_path, transport=t)
    a = {"url": "https://example.com/", "full_page": True}
    d = env.approve("s402_screenshot", a)
    assert "$0.012" in d["message"] and "/v1/screenshot/full" in d["message"]
    out = call("s402_screenshot", a)
    assert out["ok"] is True and "image_base64" not in out["result"]
    assert Path(out["result"]["image_path"]).read_bytes() == png
    assert t.paid_posts()[0]["url"].endswith("/v1/screenshot/full")


def test_bad_input_is_rejected_before_any_network_call(env):
    assert call("s402_scrape", {"url": "file:///etc/passwd"})["error"]["code"] == "bad_input"
    assert call("s402_search", {"q": ""})["error"]["code"] == "bad_input"
    assert env.transport.calls == []
