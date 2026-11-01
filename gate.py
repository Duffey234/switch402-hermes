"""Spend gate: every paid call needs a fresh human "yes" through Hermes' approval prompt.

How it works, using only public plugin hooks:

1. ``pre_tool_call`` (paid s402_* tools only): refuse outright in a cron run, when no wallet
   is configured, or when the listed price is above the per-request cap. Otherwise record a
   pending approval with a one-time reference and return ``{"action": "approve", ...}`` so
   Hermes asks the user. The rule key carries the one-time reference, so "session" or
   "always" answers never pre-approve a later call.
2. ``post_approval_response``: when the user answers that prompt with once, session or
   always, the pending entry is marked approved by a human. Smart (LLM) approvals, denials,
   timeouts and cancellations are not accepted.
3. The tool handler pays only after consuming a matching human-approved entry. If Hermes
   skipped the prompt (yolo, ``approvals.mode: off``, a cron or single-query auto-approve
   setting, a call dispatched outside the normal pipeline), there is no human answer and
   the handler refuses before anything is signed. Fails safe.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

try:
    from . import routes
except ImportError:
    import routes

PAID_TOOLS = frozenset({"s402_scrape", "s402_lite", "s402_screenshot", "s402_search", "s402_map", "s402_metadata"})
HUMAN_YES = frozenset({"once", "session", "always"})
APPROVAL_TTL_SECONDS = 600
RULE_PREFIX = "switch402:pay:"


def fingerprint(tool_name: str, args: Any) -> str:
    try:
        blob = json.dumps(args if isinstance(args, dict) else {}, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        blob = repr(args)
    return hashlib.sha256(f"{tool_name}\n{blob}".encode("utf-8")).hexdigest()


@dataclass
class Pending:
    ref: str
    scope: str
    tool: str
    fp: str
    price_atomic: int
    cap_atomic: int
    created: float
    human_approved: bool = False
    choice: str = ""


@dataclass
class SpendGate:
    cap_usd: Callable[[], float]
    in_cron: Callable[[], bool]
    has_wallet: Callable[[], bool]
    clock: Callable[[], float] = time.monotonic
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _pending: dict = field(default_factory=dict)  # ref -> Pending

    # -- helpers ------------------------------------------------------------------------------
    def cap_atomic(self, requested_usd: Any = None) -> int:
        cap = routes.usd_to_atomic(max(0.0, float(self.cap_usd())))
        if requested_usd is not None:
            try:
                cap = min(cap, routes.usd_to_atomic(max(0.0, float(requested_usd))))
            except (TypeError, ValueError):
                pass
        return cap

    def _expire(self) -> None:
        now = self.clock()
        for ref in [r for r, p in self._pending.items() if now - p.created > APPROVAL_TTL_SECONDS]:
            self._pending.pop(ref, None)

    @staticmethod
    def _scope(kwargs: dict) -> str:
        return str(kwargs.get("task_id") or kwargs.get("session_id") or "")

    # -- hook: pre_tool_call ------------------------------------------------------------------
    def pre_tool_call(self, tool_name: str = "", args: Any = None, **kwargs) -> Optional[dict]:
        if tool_name not in PAID_TOOLS:
            return None
        args = args if isinstance(args, dict) else {}
        if self.in_cron():
            return {"action": "block", "message": (
                "switch402: payments are disabled in cron and other scheduled runs. "
                "Run this from an interactive session, or use s402_quote (free).")}
        if not self.has_wallet():
            return {"action": "block", "message": (
                "switch402: SWITCH402_WALLET_KEY is not set, so paid tools are off. s402_quote still works.")}
        _key, path, price, label = routes.route_for(tool_name, args)
        cap = self.cap_atomic(args.get("max_spend_usd"))
        if price > cap:
            return {"action": "block", "message": (
                f"switch402: {path} costs ${routes.atomic_to_usd(price)} per call, above the "
                f"per-request cap of ${routes.atomic_to_usd(cap)} (max_spend_usd). Nothing was charged.")}
        ref = secrets.token_hex(6)
        with self._lock:
            self._expire()
            self._pending[ref] = Pending(ref=ref, scope=self._scope(kwargs), tool=tool_name,
                                         fp=fingerprint(tool_name, args), price_atomic=price,
                                         cap_atomic=cap, created=self.clock())
        target = args.get("q") if tool_name == "s402_search" else args.get("url")
        target = str(target or "")[:120]
        return {
            "action": "approve",
            "message": (
                f"Pay ${routes.atomic_to_usd(price)} USDC on Base to switch402 for {label} "
                f"({path}) of {target!r}? Cap ${routes.atomic_to_usd(cap)}; charged only on success. "
                f"ref {ref}"),
            "rule_key": f"{RULE_PREFIX}{ref}",
        }

    # -- hook: post_approval_response ---------------------------------------------------------
    def post_approval_response(self, choice: str = "", pattern_key: str = "", pattern_keys: Any = None,
                               description: str = "", **kwargs) -> None:
        keys = [str(pattern_key or "")] + [str(k) for k in (pattern_keys or [])]
        ref = None
        for k in keys:
            if RULE_PREFIX in k:
                ref = k.split(RULE_PREFIX, 1)[1].strip()
                break
        if ref is None and description and "ref " in str(description):
            ref = str(description).rsplit("ref ", 1)[1].strip()[:12]
        if not ref:
            return None
        with self._lock:
            p = self._pending.get(ref)
            if p is None:
                return None
            if choice in HUMAN_YES and not kwargs.get("decided_by"):
                p.human_approved = True
                p.choice = choice
            else:
                self._pending.pop(ref, None)
        return None

    # -- handler side -------------------------------------------------------------------------
    def consume(self, tool_name: str, args: Any, **kwargs) -> Pending:
        """Take the human-approved entry for this exact call, or raise PermissionError."""
        if self.in_cron():
            raise PermissionError("payments are disabled in cron and other scheduled runs")
        fp = fingerprint(tool_name, args if isinstance(args, dict) else {})
        scope = self._scope(kwargs)
        with self._lock:
            self._expire()
            match = None
            for p in sorted(self._pending.values(), key=lambda x: x.created):
                if p.tool == tool_name and p.fp == fp and p.human_approved and (not p.scope or not scope or p.scope == scope):
                    match = p
                    break
            if match is None:
                raise PermissionError(
                    "no approval from you was recorded for this paid call, so nothing was signed or paid. "
                    "Paid switch402 tools need an interactive approval prompt for every call; they do not run "
                    "under yolo, approvals off, smart approvals, cron or other unattended runs")
            self._pending.pop(match.ref, None)
            return match
