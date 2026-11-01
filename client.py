"""HTTP and x402 payment client for api.switch402.com.

Flow for one paid call: unpaid POST -> 402 with terms -> check the terms (scheme, network,
asset, resource host, amount against the approved price and the cap) -> sign one EIP-3009
USDC authorization locally -> the same POST again with the PAYMENT-SIGNATURE header.

The wallet key is read through Hermes' secret scope, used only to sign, and is never put
in a log line, an error message or a tool result.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional

try:
    from . import routes
except ImportError:  # loaded as a top-level module in tests
    import routes

USER_AGENT = "switch402-hermes/0.1.0"
WALLET_ENV = "SWITCH402_WALLET_KEY"


class Switch402Error(Exception):
    """An error whose message is safe to show the model and the user."""

    def __init__(self, code: str, message: str, *, charged: bool = False):
        super().__init__(message)
        self.code = code
        self.charged = charged


@dataclass
class HttpResponse:
    status: int
    headers: dict  # lower-cased names
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8") or "null")
        except (ValueError, UnicodeDecodeError):
            return None


def _urllib_transport(method: str, url: str, headers: dict, body: Optional[bytes], timeout: float) -> HttpResponse:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - https URL built from a constant base
            return HttpResponse(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read())
    except urllib.error.HTTPError as e:
        return HttpResponse(e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read() or b"")


def wallet_key() -> str:
    """SWITCH402_WALLET_KEY through Hermes' profile-scoped secret store (catalog rule 11)."""
    try:
        from agent.secret_scope import get_secret_str
    except ImportError:  # outside Hermes (unit tests, a plain python shell)
        return (os.environ.get(WALLET_ENV) or "").strip()
    return (get_secret_str(WALLET_ENV, "") or "").strip()


def _b64json(obj: Any) -> str:
    return base64.b64encode(json.dumps(obj, separators=(",", ":")).encode("utf-8")).decode("ascii")


def _decode_b64json(value: str) -> Any:
    try:
        return json.loads(base64.b64decode(value + "=" * (-len(value) % 4)).decode("utf-8"))
    except Exception:
        return None


def sign_exact_payment(private_key: str, payment_required: dict, accept: dict, now: Optional[int] = None) -> str:
    """Build the x402 v2 PAYMENT-SIGNATURE header value for one EIP-3009 transferWithAuthorization."""
    from eth_account import Account

    acct = Account.from_key(private_key)
    now = int(now if now is not None else time.time())
    nonce = "0x" + secrets.token_hex(32)
    authorization = {
        "from": acct.address,
        "to": accept["payTo"],
        "value": str(int(accept["amount"])),
        "validAfter": "0",
        "validBefore": str(now + int(accept.get("maxTimeoutSeconds") or 60)),
        "nonce": nonce,
    }
    extra = accept.get("extra") or {}
    domain = {
        "name": extra["name"],
        "version": extra["version"],
        "chainId": routes.CHAIN_ID,
        "verifyingContract": accept["asset"],
    }
    types = {
        "TransferWithAuthorization": [
            {"name": "from", "type": "address"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "validAfter", "type": "uint256"},
            {"name": "validBefore", "type": "uint256"},
            {"name": "nonce", "type": "bytes32"},
        ]
    }
    message = {
        "from": authorization["from"],
        "to": authorization["to"],
        "value": int(authorization["value"]),
        "validAfter": 0,
        "validBefore": int(authorization["validBefore"]),
        "nonce": bytes.fromhex(nonce[2:]),
    }
    signed = Account.sign_typed_data(private_key, domain, types, message)
    sig = signed.signature.hex()
    signature = sig if sig.startswith("0x") else "0x" + sig
    payload = {
        "x402Version": 2,
        "payload": {"authorization": authorization, "signature": signature},
        "resource": payment_required.get("resource"),
        "accepted": accept,
    }
    if payment_required.get("extensions"):
        payload["extensions"] = payment_required["extensions"]
    return _b64json(payload)


class Switch402Client:
    def __init__(self, *, base_url: str = routes.BASE_URL, timeout: float = 60,
                 transport: Optional[Callable[..., HttpResponse]] = None,
                 key_source: Callable[[], str] = wallet_key):
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self._transport = transport or _urllib_transport
        self._key_source = key_source

    # -- free ---------------------------------------------------------------------------------
    def quote(self, url: str = "", tier: str = "") -> dict:
        params = {}
        if url:
            params["url"] = url
        if tier:
            params["tier"] = tier
        qs = ("?" + urllib.parse.urlencode(params)) if params else ""
        r = self._transport("GET", f"{self.base_url}{routes.FREE_QUOTE_PATH}{qs}",
                            {"accept": "application/json", "user-agent": USER_AGENT}, None, self.timeout)
        data = r.json()
        if r.status != 200 or not isinstance(data, dict):
            raise Switch402Error("quote_failed", f"quote returned HTTP {r.status}")
        return data

    # -- paid ---------------------------------------------------------------------------------
    def has_wallet(self) -> bool:
        return bool(self._key_source())

    def _post(self, path: str, body: dict, extra_headers: Optional[dict] = None) -> HttpResponse:
        headers = {"content-type": "application/json", "accept": "application/json", "user-agent": USER_AGENT}
        headers.update(extra_headers or {})
        return self._transport("POST", f"{self.base_url}{path}", headers,
                               json.dumps(body).encode("utf-8"), self.timeout)

    @staticmethod
    def _payment_required(r: HttpResponse) -> Optional[dict]:
        hdr = r.headers.get("payment-required")
        pr = _decode_b64json(hdr) if hdr else None
        if not isinstance(pr, dict):
            pr = r.json()
        return pr if isinstance(pr, dict) else None

    @staticmethod
    def pick_accept(pr: dict, *, path: str, limit_atomic: int) -> dict:
        """The single accept that matches our route and the limit, else raise (nothing is signed)."""
        if pr.get("x402Version") != 2:
            raise Switch402Error("bad_terms", "unexpected x402 version in the 402 response")
        best = None
        for a in pr.get("accepts") or []:
            if a.get("scheme") != "exact" or a.get("network") != routes.NETWORK:
                continue
            if str(a.get("asset", "")).lower() != routes.USDC_BASE.lower():
                continue
            res = urllib.parse.urlparse(str(a.get("resource") or (pr.get("resource") or {}).get("url") or ""))
            if res.scheme != "https" or res.hostname != routes.API_HOST or res.path != path:
                continue
            extra = a.get("extra") or {}
            if not extra.get("name") or not extra.get("version") or not a.get("payTo"):
                continue
            try:
                amount = int(a.get("amount"))
            except (TypeError, ValueError):
                continue
            if amount <= 0:
                continue
            if amount > limit_atomic:
                raise Switch402Error(
                    "over_limit",
                    f"the live price ${routes.atomic_to_usd(amount)} is above what you approved or your cap "
                    f"(${routes.atomic_to_usd(limit_atomic)}); nothing was signed or paid")
            best = a
            break
        if best is None:
            raise Switch402Error("bad_terms", "no acceptable USDC-on-Base payment terms in the 402 response")
        return best

    def paid_call(self, path: str, body: dict, *, limit_atomic: int) -> dict:
        """One paid call, at most `limit_atomic` USDC units. Returns {'status','data','payment'}."""
        key = self._key_source()
        if not key:
            raise Switch402Error("no_wallet", f"{WALLET_ENV} is not set, so paid tools are off")
        r1 = self._post(path, body)
        if r1.status != 402:
            data = r1.json()
            msg = data.get("error") if isinstance(data, dict) else None
            raise Switch402Error("no_402", f"expected a 402 price response, got HTTP {r1.status}"
                                 + (f": {msg}" if msg else ""))
        pr = self._payment_required(r1)
        if not pr:
            raise Switch402Error("bad_terms", "unreadable 402 response")
        accept = self.pick_accept(pr, path=path, limit_atomic=limit_atomic)
        try:
            header = sign_exact_payment(key, pr, accept)
        except Exception:
            # Never include the exception text: it could echo key material.
            raise Switch402Error("sign_failed", f"could not sign the payment; check that {WALLET_ENV} is a valid key")
        finally:
            key = None  # noqa: F841 - drop the reference early
        r2 = self._post(path, body, {"PAYMENT-SIGNATURE": header})
        settle_hdr = r2.headers.get("payment-response") or r2.headers.get("x-payment-response")
        settlement = _decode_b64json(settle_hdr) if settle_hdr else None
        payment = {
            "amount_usd": routes.atomic_to_usd(int(accept["amount"])),
            "network": "Base",
            "asset": "USDC",
            "settled": bool(settle_hdr),
            "transaction": (settlement or {}).get("transaction") if isinstance(settlement, dict) else None,
        }
        data = r2.json()
        if not (200 <= r2.status < 300):
            msg = data.get("error") if isinstance(data, dict) else None
            raise Switch402Error("call_failed", f"paid call returned HTTP {r2.status}" + (f": {msg}" if msg else "")
                                 + ("; the service says failed calls are not charged" if not settle_hdr else ""),
                                 charged=bool(settle_hdr))
        return {"status": r2.status, "data": data, "payment": payment}
