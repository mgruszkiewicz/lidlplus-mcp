"""One-time interactive login. Opens a real Chrome window via Playwright
and waits for the user to sign into Lidl Plus by hand; once the OAuth
redirect fires we grab the authorization code and write the resulting
refresh token to disk for the poller to reuse.

We deliberately do not automate the email/password fields — Lidl's bot
protection trips on scripted credential entry, and captcha/2FA challenges
need a human anyway. The user types everything; we only collect the token."""

from __future__ import annotations

import sys

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


def main() -> int:
    cfg = load()

    profile_dir = cfg.token_file.parent / "chrome-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    print(f"Opening Lidl Plus login ({cfg.language}/{cfg.country})...")
    lidl = LidlPlusApi(language=cfg.language, country=cfg.country)
    _login(lidl, profile_dir=str(profile_dir))

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
