"""Download axe-core to vendor/axe.min.js for offline / air-gapped use.

Run once after `pip install`:
    python scripts/fetch_axe.py

Tries the npm registry first (works in most sandboxed environments), then
falls back to the configured CDN URL.
"""

from __future__ import annotations

import io
import os
import sys
import tarfile
import urllib.request

# Allow running as `python scripts/fetch_axe.py` from the project root.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from server.config import CONFIG  # noqa: E402

AXE_VERSION = "4.9.1"
NPM_TARBALL = f"https://registry.npmjs.org/axe-core/-/axe-core-{AXE_VERSION}.tgz"
UA = "A11yAuditTool/0.1 (vendor)"


def _from_npm(target: str) -> bool:
    req = urllib.request.Request(NPM_TARBALL, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            blob = resp.read()
    except Exception as exc:
        print(f"npm fetch failed: {exc}")
        return False
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        # The tarball contains package/axe.min.js
        member = next((m for m in tf.getmembers() if m.name.endswith("axe.min.js")), None)
        if not member:
            print("axe.min.js not found in tarball")
            return False
        extracted = tf.extractfile(member)
        if not extracted:
            return False
        data = extracted.read()
    with open(target, "wb") as fh:
        fh.write(data)
    print(f"Wrote {len(data)} bytes to {target} (from npm v{AXE_VERSION})")
    return True


def _from_cdn(target: str) -> bool:
    url = CONFIG.axe_cdn_url
    print(f"Falling back to CDN: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except Exception as exc:
        print(f"CDN fetch failed: {exc}")
        return False
    with open(target, "wb") as fh:
        fh.write(data)
    print(f"Wrote {len(data)} bytes to {target} (from CDN)")
    return True


def main() -> int:
    target = CONFIG.axe_script_path
    parent = os.path.dirname(target) or "."
    os.makedirs(parent, exist_ok=True)

    if _from_npm(target):
        return 0
    if _from_cdn(target):
        return 0
    print("All fetch attempts failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
