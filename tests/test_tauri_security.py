"""The window's content security policy (Codex audit AUD-003).

The Tauri window loads only the application's own files and talks only to its own IPC: no
inline script or style, no eval, no remote origin. The development policy may add only what
the Vite dev server needs (its websocket and the styles it injects).
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "src-tauri" / "tauri.conf.json"
RELEASE_OVERLAY = ROOT / "src-tauri" / "tauri.release.conf.json"
TAURI_IPC = "ipc: http://ipc.localhost"


def _security() -> dict[str, dict[str, str]]:
    security: dict[str, dict[str, str]] = json.loads(CONFIG.read_text(encoding="utf-8"))["app"][
        "security"
    ]
    return security


def test_the_production_policy_allows_only_the_app_and_its_ipc() -> None:
    csp = _security()["csp"]

    assert csp == {
        "default-src": "'self'",
        "script-src": "'self'",
        "style-src": "'self'",
        "img-src": "'self'",
        "font-src": "'self'",
        "connect-src": TAURI_IPC,
        "object-src": "'none'",
        "base-uri": "'none'",
        "form-action": "'none'",
        "frame-ancestors": "'none'",
    }


def test_no_policy_allows_eval_or_any_origin() -> None:
    for name in ("csp", "devCsp"):
        for directive, sources in _security()[name].items():
            assert "unsafe-eval" not in sources, (name, directive)
            assert "*" not in sources.split(), (name, directive)
            assert "http:" not in sources.split() and "https:" not in sources.split()


def test_the_dev_policy_adds_only_what_the_vite_dev_server_needs() -> None:
    security = _security()
    production, dev = security["csp"], security["devCsp"]

    differences = {k: v for k, v in dev.items() if production.get(k) != v}

    assert set(dev) == set(production)
    assert differences == {
        "style-src": "'self' 'unsafe-inline'",
        "connect-src": f"{TAURI_IPC} ws://localhost:1420",
    }


def test_the_release_overlay_keeps_the_policy() -> None:
    overlay = json.loads(RELEASE_OVERLAY.read_text(encoding="utf-8"))

    assert "security" not in overlay.get("app", {})
