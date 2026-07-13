"""Download axe-core to vendor/axe.min.js for offline / air-gapped use.

Run once after `pip install`:
    python scripts/fetch_axe.py

Tries the npm registry first (works in most sandboxed environments), then
falls back to the configured CDN URL.
"""

from __future__ import annotations

import io
import hashlib
import os
import sys
import tarfile
import tempfile
import urllib.request
from urllib.parse import urlparse

# Allow running as `python scripts/fetch_axe.py` from the project root.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from server.config import AXE_VERSION, CONFIG  # noqa: E402

NPM_TARBALL = f"https://registry.npmjs.org/axe-core/-/axe-core-{AXE_VERSION}.tgz"
# npm's published integrity for axe-core 4.12.1 and the extracted script hash.
# Pinning both protects the build and CDN fallback from silent supply-chain
# substitution. Update these alongside AXE_VERSION.
NPM_TARBALL_SHA512 = (
    "b3b8867f919a54cc441b410d37dc7ec53afb1856426f564fff5b804d4a4210ad"
    "97efc9c30774706ed142a3da4601ff6bbbe570a10e3ae0391a2c4791334f3024"
)
AXE_SCRIPT_SHA256 = "66a8aaa95a8b044a7fd74a5435873bf04ff65a1ca75567c921b7509742085a14"
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
UA = "A11yAuditTool/0.1 (vendor)"


def _verified_script(data: bytes) -> bytes | None:
    if len(data) > MAX_DOWNLOAD_BYTES:
        print(f"axe script exceeds {MAX_DOWNLOAD_BYTES} byte safety cap")
        return None
    digest = hashlib.sha256(data).hexdigest()
    if digest != AXE_SCRIPT_SHA256:
        print(f"axe script checksum mismatch: expected {AXE_SCRIPT_SHA256}, got {digest}")
        return None
    return data


def _https_request(url: str) -> urllib.request.Request:
    if urlparse(url).scheme.lower() != "https":
        raise ValueError("axe downloads require an https URL")
    return urllib.request.Request(url, headers={"User-Agent": UA})


def _write_atomic(target: str, data: bytes) -> None:
    parent = os.path.dirname(target) or "."
    fd, temporary = tempfile.mkstemp(prefix="axe-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _from_npm(target: str) -> bool:
    req = _https_request(NPM_TARBALL)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            blob = resp.read(MAX_DOWNLOAD_BYTES + 1)
    except Exception as exc:
        print(f"npm fetch failed: {exc}")
        return False
    if len(blob) > MAX_DOWNLOAD_BYTES:
        print("npm tarball exceeds download safety cap")
        return False
    tar_digest = hashlib.sha512(blob).hexdigest()
    if tar_digest != NPM_TARBALL_SHA512:
        print(f"npm tarball checksum mismatch: {tar_digest}")
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
        data = extracted.read(MAX_DOWNLOAD_BYTES + 1)
    data = _verified_script(data)
    if data is None:
        return False
    _write_atomic(target, data)
    print(f"Wrote {len(data)} bytes to {target} (from npm v{AXE_VERSION})")
    return True


def _from_cdn(target: str) -> bool:
    url = CONFIG.axe_cdn_url
    print(f"Falling back to CDN: {url}")
    try:
        req = _https_request(url)
    except ValueError as exc:
        print(f"CDN URL rejected: {exc}")
        return False
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            data = resp.read(MAX_DOWNLOAD_BYTES + 1)
    except Exception as exc:
        print(f"CDN fetch failed: {exc}")
        return False
    data = _verified_script(data)
    if data is None:
        return False
    _write_atomic(target, data)
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
