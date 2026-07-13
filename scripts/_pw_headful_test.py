"""Check which launchable browsers this machine has via Playwright channels."""
from playwright.sync_api import sync_playwright


def try_channel(pw, channel=None, headless=False):
    label = channel or "bundled"
    try:
        b = pw.chromium.launch(headless=headless, channel=channel) if channel else pw.chromium.launch(headless=headless)
        print(f"[{label}] launched (headless={headless})")
        b.close()
        return True
    except Exception as e:
        msg = str(e).splitlines()[0][:120]
        print(f"[{label}] FAILED: {msg}")
        return False


with sync_playwright() as pw:
    for ch in (None, "chrome", "msedge", "chromium"):
        try_channel(pw, ch, headless=False)
