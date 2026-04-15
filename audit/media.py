"""Media module: images, video, audio.

Rules implemented:
- media-img-no-alt           WCAG 1.1.1  critical  <img> missing alt
- media-img-placeholder-alt  WCAG 1.1.1  moderate  alt looks like a filename / generic placeholder
- media-img-decorative-text  WCAG 1.1.1  minor     decorative img (role=presentation / alt="") has a non-empty alt
- media-video-no-track       WCAG 1.2.2  serious   <video> has no <track kind="captions">
- media-autoplay             WCAG 1.4.2  serious   <audio>/<video> with autoplay and no muted
"""

from __future__ import annotations

import re
import time
from typing import Any

from audit._issue import make_issue

PLACEHOLDER_PATTERNS = [
    re.compile(r"^(image|photo|picture|img|icon|logo)\.?$", re.IGNORECASE),
    re.compile(r"\.(jpe?g|png|gif|webp|svg|bmp)$", re.IGNORECASE),
    re.compile(r"^\s*$"),
    re.compile(r"^(img|image|photo|dsc)[_-]?\d+", re.IGNORECASE),
]


_EXTRACT_JS = r"""
() => {
    function cssPath(el) {
        if (!el || el.nodeType !== 1) return '';
        if (el.id) return '#' + el.id;
        const parts = [];
        let cur = el;
        while (cur && cur.nodeType === 1 && cur.tagName.toLowerCase() !== 'html') {
            let part = cur.tagName.toLowerCase();
            const parent = cur.parentElement;
            if (parent) {
                const sameTag = [...parent.children].filter(c => c.tagName === cur.tagName);
                if (sameTag.length > 1) {
                    part += ':nth-of-type(' + (sameTag.indexOf(cur) + 1) + ')';
                }
            }
            parts.unshift(part);
            cur = cur.parentElement;
            if (parts.length > 6) break;
        }
        return parts.join(' > ');
    }
    const images = [...document.querySelectorAll('img')].map(img => ({
        alt: img.getAttribute('alt'),
        src: img.getAttribute('src') || '',
        role: img.getAttribute('role'),
        aria_hidden: img.getAttribute('aria-hidden') === 'true',
        selector: cssPath(img),
        html: img.outerHTML.slice(0, 200)
    }));
    const videos = [...document.querySelectorAll('video')].map(v => ({
        has_caption_track: v.querySelector('track[kind="captions"], track[kind="subtitles"]') !== null,
        autoplay: v.hasAttribute('autoplay'),
        muted: v.hasAttribute('muted') || v.muted,
        selector: cssPath(v),
        html: v.outerHTML.slice(0, 200)
    }));
    const audios = [...document.querySelectorAll('audio')].map(a => ({
        autoplay: a.hasAttribute('autoplay'),
        muted: a.hasAttribute('muted') || a.muted,
        selector: cssPath(a),
        html: a.outerHTML.slice(0, 200)
    }));
    return { images, videos, audios };
}
"""


def _looks_like_placeholder(alt: str) -> bool:
    trimmed = (alt or "").strip()
    if not trimmed:
        return False
    return any(p.search(trimmed) for p in PLACEHOLDER_PATTERNS)


def analyze(dom: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    for idx, img in enumerate(dom.get("images") or []):
        alt = img.get("alt")
        role = (img.get("role") or "").lower()
        decorative = role == "presentation" or role == "none" or img.get("aria_hidden")

        if alt is None and not decorative:
            issues.append(
                make_issue(
                    issue_id=f"media-img-no-alt-{idx}",
                    module="media",
                    rule="media-img-no-alt",
                    severity="critical",
                    wcag=["1.1.1"],
                    title="<img> missing alt attribute",
                    description=(
                        "Images with no alt attribute are announced by screen readers "
                        "as the raw file path or not at all."
                    ),
                    selector=img.get("selector", ""),
                    html_snippet=img.get("html", ""),
                    details={"src": img.get("src", "")},
                    fix='Add alt="describe the image" or alt="" for purely decorative images.',
                )
            )
            continue

        if alt is not None and not decorative and _looks_like_placeholder(alt):
            issues.append(
                make_issue(
                    issue_id=f"media-img-placeholder-alt-{idx}",
                    module="media",
                    rule="media-img-placeholder-alt",
                    severity="moderate",
                    wcag=["1.1.1"],
                    title="Image alt text looks like a placeholder or filename",
                    description=(
                        "Alt text such as 'image.jpg', 'IMG_1234', or 'photo' does not "
                        "describe the image to screen-reader users."
                    ),
                    selector=img.get("selector", ""),
                    html_snippet=img.get("html", ""),
                    text=alt,
                    details={"alt": alt, "src": img.get("src", "")},
                    fix="Replace the alt with a short description of the image's purpose.",
                )
            )

        if decorative and (alt or "").strip():
            issues.append(
                make_issue(
                    issue_id=f"media-img-decorative-text-{idx}",
                    module="media",
                    rule="media-img-decorative-text",
                    severity="minor",
                    wcag=["1.1.1"],
                    title="Decorative image has non-empty alt text",
                    description=(
                        'An image marked role="presentation" or aria-hidden should have '
                        "empty alt text so screen readers skip it."
                    ),
                    selector=img.get("selector", ""),
                    html_snippet=img.get("html", ""),
                    text=alt,
                    details={"alt": alt, "role": role},
                    fix='Use alt="" on decorative images.',
                )
            )

    for idx, v in enumerate(dom.get("videos") or []):
        if not v.get("has_caption_track"):
            issues.append(
                make_issue(
                    issue_id=f"media-video-no-track-{idx}",
                    module="media",
                    rule="media-video-no-track",
                    severity="serious",
                    wcag=["1.2.2"],
                    title="<video> has no caption track",
                    description=(
                        "Prerecorded video content needs synchronized captions for "
                        "users who cannot hear the audio."
                    ),
                    selector=v.get("selector", ""),
                    html_snippet=v.get("html", ""),
                    fix='Add <track kind="captions" srclang="en" src="captions.vtt"> inside <video>.',
                )
            )
        if v.get("autoplay") and not v.get("muted"):
            issues.append(
                make_issue(
                    issue_id=f"media-autoplay-video-{idx}",
                    module="media",
                    rule="media-autoplay",
                    severity="serious",
                    wcag=["1.4.2"],
                    title="<video> autoplays with audio",
                    description=(
                        "Auto-playing audio interferes with screen readers and can "
                        "startle users. WCAG 1.4.2 requires a mechanism to pause, stop, "
                        "or mute."
                    ),
                    selector=v.get("selector", ""),
                    html_snippet=v.get("html", ""),
                    fix="Remove autoplay, or add muted plus a visible play/pause control.",
                )
            )

    for idx, a in enumerate(dom.get("audios") or []):
        if a.get("autoplay") and not a.get("muted"):
            issues.append(
                make_issue(
                    issue_id=f"media-autoplay-audio-{idx}",
                    module="media",
                    rule="media-autoplay",
                    severity="serious",
                    wcag=["1.4.2"],
                    title="<audio> autoplays",
                    description=(
                        "Auto-playing audio longer than 3 seconds violates WCAG 1.4.2 "
                        "unless a mechanism to pause or stop it is provided."
                    ),
                    selector=a.get("selector", ""),
                    html_snippet=a.get("html", ""),
                    fix="Remove the autoplay attribute or ensure a visible stop control exists.",
                )
            )

    return issues


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    start = time.time()
    try:
        dom = page.evaluate(_EXTRACT_JS)
    except Exception as exc:
        return {
            "ran": False,
            "error": str(exc),
            "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }
    issues = analyze(dom)
    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "images": len(dom.get("images") or []),
        "videos": len(dom.get("videos") or []),
        "audios": len(dom.get("audios") or []),
    }
