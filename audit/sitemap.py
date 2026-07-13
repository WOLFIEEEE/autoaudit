"""Sitemap.xml + robots.txt Sitemap: discovery and URL sampling.

Enterprise audits want "scan the top N pages" without the caller
enumerating URLs by hand. This module:

  1. fetches /sitemap.xml (or a robots.txt-declared Sitemap: URL)
  2. parses nested <sitemapindex> / <urlset> structures
  3. samples up to N URLs using a deterministic strategy (homepage
     first, then breadth-balanced across path prefixes)

We stay explicit about the tradeoffs:
  - Sitemap.xml is a *publishing* artifact; missing URLs are missing
    from audit scope. Stakeholders should manually list high-value
    URLs the sitemap omits.
  - We deliberately do NOT crawl the site (no BFS link following).
    That would blow past rate limits and hit pages the site owner
    may not want audited (admin pages, search result permutations).
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from urllib.parse import urljoin, urlparse

from defusedxml import ElementTree as ET

from server.models import validate_public_http_url

log = logging.getLogger(__name__)

# XML namespace used by sitemap.org-compliant sitemaps. We expose the
# bare tag names too because some sites omit the namespace declaration.
_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def discover_urls(
    root_url: str,
    *,
    max_urls: int = 10,
    max_fetch: int = 5,
    fetch_fn=None,
) -> list[str]:
    """Return up to `max_urls` URLs discovered starting from `root_url`.

    `fetch_fn(url) -> str | None` is injected so tests can avoid the
    network. Defaults to an httpx-backed fetcher when not supplied.

    `max_fetch` caps the number of sitemap documents we'll parse (so
    a nested sitemapindex with thousands of children doesn't explode).
    """
    if fetch_fn is None:
        fetch_fn = _default_fetch

    sitemap_urls = _candidate_sitemap_urls(root_url, fetch_fn)
    seen: list[str] = []
    visited_sitemaps: set[str] = set()
    for sm_url in sitemap_urls:
        if len(visited_sitemaps) >= max_fetch:
            break
        _walk_sitemap(sm_url, fetch_fn, visited_sitemaps, seen, max_fetch)

    if not seen:
        # Sitemap absent or empty: fall back to just the root URL so
        # callers always get something.
        seen = [root_url]

    return sample_urls(seen, n=max_urls, root_url=root_url)


_NUMERIC_SEGMENT_RE = re.compile(r"^\d+$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,}$")


def url_template(url: str) -> str:
    """Normalise a URL to its 'template' shape for clustering.

    Replaces:
      - numeric path segments        -> {n}
      - UUID-shaped segments         -> {uuid}
      - long slug segments (>=3 dashes) -> {slug}

    Example:
      /products/12345/reviews/item-name-detail
        -> /products/{n}/reviews/{slug}

    Used by `sample_by_template` to pick one representative URL per
    template family — auditing 200 product pages all derived from the
    same template wastes time, since the template defects manifest on
    page 1 and never change. Auditing one product page + the unique
    landing-pages catches the same defect set in 5% of the run time.
    """
    parsed = urlparse(url)
    segments = []
    for seg in parsed.path.split("/"):
        if not seg:
            segments.append(seg)
            continue
        low = seg.lower()
        if _NUMERIC_SEGMENT_RE.match(low):
            segments.append("{n}")
        elif _UUID_RE.match(low):
            segments.append("{uuid}")
        elif _SLUG_RE.match(low):
            segments.append("{slug}")
        else:
            segments.append(seg)
    template = "/".join(segments)
    # Query strings are typically ID-bearing too; keep their keys but
    # drop their values to bucket /search?q=foo with /search?q=bar.
    if parsed.query:
        keys = sorted(set(p.split("=", 1)[0] for p in parsed.query.split("&") if p))
        template += "?" + ("&".join(keys))
    return template


def sample_by_template(
    urls: list[str], *, n: int, root_url: str | None = None,
) -> list[str]:
    """Sample at most `n` URLs, taking one representative per template.

    Falls back to `sample_urls` after deduplication when more than `n`
    distinct templates exist (which is usually the case on real
    sites — categories, blog posts, product pages, docs all have
    their own template). Within a template bucket we keep the URL
    that's *deepest* (most segments) on the assumption that deeper
    pages exercise more of the template's branches.
    """
    if n <= 0 or not urls:
        return []
    by_template: dict[str, str] = {}
    for u in urls:
        tmpl = url_template(u)
        existing = by_template.get(tmpl)
        if existing is None:
            by_template[tmpl] = u
            continue
        # Prefer the deepest URL — it tends to exercise the most
        # template branches (e.g. /blog/post/comments over /blog).
        if u.count("/") > existing.count("/"):
            by_template[tmpl] = u
    representatives = list(by_template.values())
    return sample_urls(representatives, n=n, root_url=root_url)


def sample_urls(urls: list[str], *, n: int, root_url: str | None = None) -> list[str]:
    """Pick `n` URLs using a breadth-balanced strategy.

    Strategy:
      1. Homepage (root_url) first if it's in the pool.
      2. Up to ceil(n/k) URLs from each distinct first-segment bucket
         (/products, /blog, /docs, ...) so we spread across sections.
      3. Deterministic order within a bucket (lexical by URL).
    """
    if n <= 0 or not urls:
        return []
    # Deduplicate preserving earliest-seen order.
    uniq: list[str] = []
    seen_set: set[str] = set()
    for u in urls:
        if u not in seen_set:
            uniq.append(u)
            seen_set.add(u)

    ordered: list[str] = []
    root_path = urlparse(root_url).path.rstrip("/") if root_url else ""
    if root_url and root_url in seen_set:
        ordered.append(root_url)
        uniq.remove(root_url)

    # Bucket by first path segment.
    buckets: dict[str, list[str]] = defaultdict(list)
    for u in uniq:
        seg = urlparse(u).path.strip("/").split("/", 1)[0] or "_root_"
        if seg == root_path.strip("/"):
            seg = "_root_"
        buckets[seg].append(u)

    # Round-robin draw: one URL per bucket until we hit n.
    bucket_keys = sorted(buckets)
    idx_per_bucket = {k: 0 for k in bucket_keys}
    while len(ordered) < n and any(
        idx_per_bucket[k] < len(buckets[k]) for k in bucket_keys
    ):
        for k in bucket_keys:
            if len(ordered) >= n:
                break
            i = idx_per_bucket[k]
            if i < len(buckets[k]):
                ordered.append(buckets[k][i])
                idx_per_bucket[k] += 1

    return ordered[:n]


# --------------------------------------------------------------------
# Internals


def _candidate_sitemap_urls(root_url: str, fetch_fn) -> list[str]:
    """Find sitemap URLs to try, honoring robots.txt Sitemap:
    directives first, then falling back to /sitemap.xml."""
    candidates: list[str] = []
    parsed = urlparse(root_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    robots_txt = fetch_fn(urljoin(origin, "/robots.txt")) or ""
    for line in robots_txt.splitlines():
        line = line.strip()
        m = re.match(r"Sitemap:\s*(\S+)", line, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            # robots.txt can declare any URL — including file://, ftp://,
            # gopher://. Reject everything except http(s) so a malicious
            # robots.txt cannot redirect the fetcher at the local fs or
            # an internal service.
            if candidate.lower().startswith(("http://", "https://")):
                candidates.append(candidate)
            else:
                log.debug("ignoring non-http(s) Sitemap: %r", candidate)

    if not candidates:
        candidates.append(urljoin(origin, "/sitemap.xml"))
    return candidates


def _walk_sitemap(
    url: str,
    fetch_fn,
    visited: set[str],
    out_urls: list[str],
    max_fetch: int,
) -> None:
    if url in visited or len(visited) >= max_fetch:
        return
    visited.add(url)
    body = fetch_fn(url)
    if not body:
        return
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        log.debug("non-XML response at %s; skipping", url)
        return

    # sitemapindex -> nested sitemaps
    for sm in _iter(root, "sitemap"):
        loc = _text(sm, "loc")
        if loc:
            _walk_sitemap(loc, fetch_fn, visited, out_urls, max_fetch)
    # urlset -> page URLs
    for u in _iter(root, "url"):
        loc = _text(u, "loc")
        if loc:
            out_urls.append(loc)


def _iter(root: ET.Element, local: str):
    # Match either namespaced or bare tag, whichever the doc uses.
    yield from root.findall(f"{{{_SITEMAP_NS}}}{local}")
    yield from root.findall(local)


def _text(el: ET.Element, local: str) -> str:
    for ns in (f"{{{_SITEMAP_NS}}}{local}", local):
        child = el.find(ns)
        if child is not None and child.text:
            return child.text.strip()
    return ""


def _default_fetch(url: str) -> str | None:
    """Default HTTP fetcher. Lazy-imports httpx so tests that inject
    their own fetch_fn don't require the dependency."""
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a project dep
        return None
    current = url
    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=10,
            headers={"User-Agent": "AutoauditSitemap/1.0 (+a11y audit tool)"},
        ) as client:
            for _ in range(6):
                validate_public_http_url(current, label="sitemap URL")
                with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            return None
                        current = urljoin(current, location)
                        continue
                    if response.status_code >= 400:
                        return None
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > 5 * 1024 * 1024:
                            log.warning("sitemap response exceeds 5 MiB; skipping")
                            return None
                        chunks.append(chunk)
                    return b"".join(chunks).decode(response.encoding or "utf-8", "replace")
            log.warning("sitemap redirect limit exceeded for %s", url)
            return None
    except Exception as exc:
        log.debug("sitemap fetch failed for %s: %s", url, exc)
        return None
