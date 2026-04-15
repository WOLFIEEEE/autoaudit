"""Download axe-core to vendor/axe.min.js for offline / air-gapped use.

Run once after `pip install`:
    python scripts/fetch_axe.py
"""

from __future__ import annotations

import os
import sys
import urllib.request

from server.config import CONFIG


def main() -> int:
    url = CONFIG.axe_cdn_url
    target = CONFIG.axe_script_path
    parent = os.path.dirname(target) or "."
    os.makedirs(parent, exist_ok=True)

    print(f"Fetching {url} -> {target}")
    req = urllib.request.Request(url, headers={"User-Agent": "A11yAuditTool/0.1 (vendor)"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - explicit trusted URL
        data = resp.read()

    with open(target, "wb") as fh:
        fh.write(data)

    print(f"Wrote {len(data)} bytes to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
