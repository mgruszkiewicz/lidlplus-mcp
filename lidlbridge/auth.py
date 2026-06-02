"""One-time interactive login. Opens a real Chrome window via Playwright
and waits for the user to sign into Lidl Plus by hand; once the OAuth
redirect fires we grab the authorization code and write the resulting
refresh token to disk for the poller to reuse.

We deliberately do not automate the email/password fields — Lidl's bot
protection trips on scripted credential entry, and captcha/2FA challenges
need a human anyway. The user types everything; we only collect the token.

When even the visible Playwright window trips bot protection, ``--manual``
(or the automatic fallback when the automated attempt fails) skips Playwright
entirely: the user logs in in their own everyday browser and pastes back the
final ``com.lidlplus.app://callback?code=...`` redirect so we can exchange the
authorization code ourselves."""

from __future__ import annotations

import argparse
import sys
from urllib.parse import parse_qs

from lidlplus_api import LidlPlusApi
from playwright.sync_api import sync_playwright

from .config import load


_STEALTH_INIT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['pl-PL', 'pl', 'en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {} };
const origQuery = window.navigator.permissions && window.navigator.permissions.query;
if (origQuery) {
  window.navigator.permissions.query = (p) =>
    p && p.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : origQuery(p);
}
"""

# Generous timeout so the user has time to type credentials and clear any
# captcha / 2FA challenge before the OAuth redirect we're waiting on fires.
_LOGIN_TIMEOUT_MS = 10 * 60 * 1000


def _login(lidl: LidlPlusApi, profile_dir: str) -> None:
    """Open Lidl's login page and wait for a human to sign in.

    Navigates to the OAuth authorize link (which redirects to the login
    form) and then blocks in ``_parse_code`` until the user completes the
    sign-in by hand. ``_parse_code`` watches for the ``connect/authorize``
    callback and extracts the authorization code from its ``location``
    header, so it doesn't matter how the form got filled. A persistent
    Chrome profile plus light stealth tweaks keep cookies/device-trust
    around and reduce the chance of tripping automation detection.
    """
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            channel="chrome",
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="pl-PL",
            timezone_id="Europe/Warsaw",
            ignore_default_args=["--enable-automation"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-default-browser-check",
            ],
        )
        context.add_init_script(_STEALTH_INIT)
        context.set_default_timeout(_LOGIN_TIMEOUT_MS)

        page = context.new_page()
        page.goto(lidl._register_link)

        print(
            "Sign in to Lidl Plus in the browser window that just opened — "
            "enter your email, password, and clear any captcha/2FA challenge "
            "by hand. We'll keep waiting up to 10 minutes for the redirect, "
            "then grab the token automatically.",
            file=sys.stderr,
        )

        authcode = lidl._parse_code(page)
        context.close()
        lidl._authorization_code(authcode)


def _extract_code(pasted: str) -> str:
    """Pull the OAuth ``code`` out of a pasted callback URL or bare code.

    The redirect we want the user to copy looks like
    ``com.lidlplus.app://callback?code=XXXX&scope=...``. urllib chokes on the
    custom scheme, so we just split off the query string and parse that. If the
    user pasted only the code itself, we return it unchanged.
    """
    pasted = pasted.strip()
    if "?" in pasted:
        params = parse_qs(pasted.split("?", 1)[1])
        if params.get("code"):
            return params["code"][0]
    return pasted


def _manual_login(lidl: LidlPlusApi) -> None:
    """Complete login in the user's own browser, then exchange the code.

    This is the fallback for when Lidl's bot protection blocks even the
    visible Playwright window. Reading ``_register_link`` seeds the PKCE
    ``code_verifier`` on this instance; because we reuse the same instance for
    the token exchange, it doesn't matter which browser the user signs in with
    — we only need the authorization code from the final redirect.
    """
    # _register_link leaves raw spaces in the `scope` param; a browser splits
    # the URL on them and tries to open the embedded com.lidlplus.app:// chunk
    # as a protocol ("Nieznany protokół"). Percent-encode them so it pastes as
    # one URL. The server accepts the %20-encoded scope identically.
    link = lidl._register_link.replace(" ", "%20")
    print(
        "\nManual login\n"
        "------------\n"
        "1. Open this URL in your normal browser (copy it exactly):\n\n"
        f"   {link}\n\n"
        "2. Sign in to Lidl Plus and clear any captcha / 2FA by hand.\n"
        "3. After the final sign-in the browser tries to open\n"
        "   com.lidlplus.app://callback?code=... — it can't follow that custom\n"
        "   scheme, so it shows an error or an 'open app?' prompt. That's fine.\n"
        "4. Copy that whole com.lidlplus.app://callback?code=... URL — it stays\n"
        "   in the address bar (Firefox is most reliable here; in Chrome check\n"
        "   DevTools > Network for the blocked request) — and paste it below.\n",
        file=sys.stderr,
    )
    code = _extract_code(input("Paste the callback URL (or just the code): "))
    if not code:
        print("No authorization code found in what you pasted.", file=sys.stderr)
        raise SystemExit(2)
    lidl._authorization_code(code)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="lidl-auth",
        description="One-time Lidl Plus login that saves a refresh token.",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Skip the automated browser and complete login in your own "
        "browser (use when bot protection blocks the Playwright window).",
    )
    parsed = parser.parse_args()

    cfg = load()

    profile_dir = cfg.token_file.parent / "chrome-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    lidl = LidlPlusApi(language=cfg.language, country=cfg.country)

    if parsed.manual:
        _manual_login(lidl)
    else:
        print(f"Opening Lidl Plus login ({cfg.language}/{cfg.country})...")
        try:
            _login(lidl, profile_dir=str(profile_dir))
        except Exception as exc:  # bot protection, timeout, Chrome missing, ...
            print(f"\nAutomated login failed: {exc}", file=sys.stderr)
            print("Falling back to manual login in your own browser.", file=sys.stderr)
            _manual_login(lidl)

    token = lidl.refresh_token
    if not token:
        print("Login did not return a refresh token.", file=sys.stderr)
        return 2

    cfg.token_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.token_file.write_text(token)
    cfg.token_file.chmod(0o600)
    print(f"Refresh token saved to {cfg.token_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
