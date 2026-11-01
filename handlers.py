"""Tool handlers. Every handler returns a JSON string and never raises."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from . import routes
    from .client import Switch402Client, Switch402Error
    from .gate import SpendGate
except ImportError:
    import routes
    from client import Switch402Client, Switch402Error
    from gate import SpendGate

PLUGIN_NAME = "switch402"


class Runtime:
    """Holds the client and spend gate built at register() time (tests build their own)."""

    def __init__(self, client: Switch402Client, gate: SpendGate,
                 data_dir: Optional[Callable[[], Path]] = None):
        self.client = client
        self.gate = gate
        self._data_dir = data_dir

    def data_dir(self) -> Path:
        if self._data_dir is not None:
            return self._data_dir()
        from plugins.plugin_storage import plugin_data_dir
        return plugin_data_dir(PLUGIN_NAME)


_RT: Optional[Runtime] = None


def set_runtime(rt: Runtime) -> None:
    global _RT
    _RT = rt


def _rt() -> Runtime:
    if _RT is None:
        raise Switch402Error("not_ready", "switch402 plugin is not initialised")
    return _RT


def _ok(**payload) -> str:
    return json.dumps({"ok": True, **payload})


def _err(code: str, message: str, **extra) -> str:
    return json.dumps({"ok": False, "error": {"code": code, "message": message}, **extra})


def _validate(tool: str, args: dict) -> Optional[str]:
    if tool == "s402_search":
        q = str(args.get("q") or "").strip()
        if not q or len(q) > 400:
            return "q must be 1-400 characters"
        return None
    url = str(args.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")) or len(url) > 2048:
        return "url must be a public http(s) URL up to 2048 characters"
    return None


# -- free -------------------------------------------------------------------------------------
def s402_quote(args: dict, **kwargs) -> str:
    try:
        args = args or {}
        tier = str(args.get("tier") or "")
        if tier and tier not in ("standard", "lite"):
            return _err("bad_input", "tier must be standard or lite")
        data = _rt().client.quote(url=str(args.get("url") or ""), tier=tier)
        prices = {name: f"${routes.atomic_to_usd(p)}" for name, (_path, p, _l) in routes.PAID_ROUTES.items()}
        return _ok(quote=data, list_prices_usd=prices, note="Free. No wallet needed.")
    except Switch402Error as e:
        return _err(e.code, str(e))
    except Exception as e:  # network errors etc.; never include secrets (none are in scope here)
        return _err("quote_failed", f"quote failed: {type(e).__name__}")


# -- paid -------------------------------------------------------------------------------------
def _paid(tool: str, args: dict, kwargs: dict) -> str:
    args = args if isinstance(args, dict) else {}
    problem = _validate(tool, args)
    if problem:
        return _err("bad_input", problem + "; nothing was charged")
    rt = _rt()
    try:
        approval = rt.gate.consume(tool, args, **kwargs)
    except PermissionError as e:
        return _err("not_approved", str(e))
    _key, path, _price, _label = routes.route_for(tool, args)
    # Never more than the user approved, and never more than the cap (re-read now).
    limit = min(approval.price_atomic, approval.cap_atomic, rt.gate.cap_atomic(args.get("max_spend_usd")))
    try:
        res = rt.client.paid_call(path, routes.request_body(tool, args), limit_atomic=limit)
    except Switch402Error as e:
        return _err(e.code, str(e), charged=e.charged)
    except Exception as e:
        return _err("call_failed", f"request failed: {type(e).__name__}; check the payment field before retrying",
                    charged=None)
    data = res["data"]
    if tool == "s402_screenshot" and isinstance(data, dict) and data.get("image_base64"):
        try:
            png = base64.b64decode(data["image_base64"])
            out_dir = rt.data_dir() / "screenshots"
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"shot-{int(time.time() * 1000)}.png"
            out.write_bytes(png)
            data = {k: v for k, v in data.items() if k != "image_base64"}
            data["image_path"] = str(out)
        except Exception as e:
            data = {k: v for k, v in data.items() if k != "image_base64"}
            data["image_note"] = f"screenshot was paid for but could not be saved: {type(e).__name__}"
    return _ok(result=data, payment=res["payment"])


def s402_scrape(args: dict, **kwargs) -> str:
    return _paid("s402_scrape", args, kwargs)


def s402_lite(args: dict, **kwargs) -> str:
    return _paid("s402_lite", args, kwargs)


def s402_screenshot(args: dict, **kwargs) -> str:
    return _paid("s402_screenshot", args, kwargs)


def s402_search(args: dict, **kwargs) -> str:
    return _paid("s402_search", args, kwargs)


def s402_map(args: dict, **kwargs) -> str:
    return _paid("s402_map", args, kwargs)


def s402_metadata(args: dict, **kwargs) -> str:
    return _paid("s402_metadata", args, kwargs)
