"""Visual module — static rules only (no image processing).

Scoped to three rules that are unambiguous from static DOM + computed styles:

- visual-marquee-or-blink     WCAG 2.2.2  serious   <marquee> or <blink> element present
- visual-infinite-animation   WCAG 2.2.2  moderate  CSS animation with infinite iteration count
- visual-tiny-text            WCAG 1.4.4  minor     visible text with computed font-size < 9px

Contrast checking is intentionally NOT implemented here — axe-core (wcag_engine
module) already covers it well, including the hard cases (partial transparency,
gradient backgrounds, images-of-text). Duplicating it would add noise, not
signal; the deduplicator handles the overlap for the one rule we share.

Reflow at 320px and color-blindness simulation are deferred: both require
a screenshot pipeline (Pillow / numpy) and meaningful page interaction,
better done as a dedicated interactive pass.
"""

from __future__ import annotations

import time
from typing import Any

from audit._issue import make_issue

MIN_FONT_SIZE_PX = 9.0
# Animations shorter than this may be legitimate micro-interactions.
MIN_ANIMATION_DURATION_S = 0.5


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
    function isVisible(el, style) {
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        if (parseFloat(style.opacity) === 0) return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    }

    // marquee / blink
    const marquee = [...document.querySelectorAll('marquee, blink')].map(el => ({
        tag: el.tagName.toLowerCase(),
        selector: cssPath(el),
        html: el.outerHTML.slice(0, 200)
    }));

    // Scan first 5000 elements for infinite animations and tiny text.
    const all = document.querySelectorAll('*');
    const limit = Math.min(all.length, 5000);
    const infinite_animations = [];
    const tiny_text = [];

    // Collect parents of non-empty text nodes (dedup via Set).
    const textParents = new Set();
    const walker = document.createTreeWalker(document.body || document, NodeFilter.SHOW_TEXT);
    let node;
    while (node = walker.nextNode()) {
        const t = (node.nodeValue || '').trim();
        if (!t) continue;
        if (node.parentElement) textParents.add(node.parentElement);
        if (textParents.size >= 5000) break;
    }

    for (let i = 0; i < limit; i++) {
        const el = all[i];
        const style = getComputedStyle(el);
        const iter = style.animationIterationCount || '';
        const durS = parseFloat(style.animationDuration || '0');
        if (iter.split(',').some(t => t.trim() === 'infinite') && durS > 0 && isVisible(el, style)) {
            infinite_animations.push({
                tag: el.tagName.toLowerCase(),
                selector: cssPath(el),
                html: el.outerHTML.slice(0, 200),
                animation_name: style.animationName,
                duration_s: durS
            });
        }
    }

    for (const el of textParents) {
        const style = getComputedStyle(el);
        if (!isVisible(el, style)) continue;
        const sz = parseFloat(style.fontSize || '0');
        if (sz > 0 && sz < 9.0) {
            tiny_text.push({
                selector: cssPath(el),
                html: el.outerHTML.slice(0, 200),
                font_size_px: sz,
                tag: el.tagName.toLowerCase()
            });
        }
    }

    // Autoplaying media with sound (WCAG 1.4.2 Audio Control, A):
    // any <audio autoplay> or <video autoplay> without muted AND
    // without controls presents an immediate audio stream the user
    // can't silence except by leaving the page.
    const autoplay_media = [];
    for (const el of [...document.querySelectorAll('audio[autoplay], video[autoplay]')]) {
        const muted = el.muted || el.hasAttribute('muted');
        const controls = el.hasAttribute('controls');
        if (muted) continue;
        autoplay_media.push({
            tag: el.tagName.toLowerCase(),
            has_controls: controls,
            selector: cssPath(el),
            html: el.outerHTML.slice(0, 200),
        });
    }

    // Carousels that rotate automatically without pause control.
    // We detect: an element whose aria-live=off OR role=region with
    // an obvious "carousel"/"slider" token in its class/id, containing
    // a setInterval-scheduled rotation (impossible to detect 100% from
    // static CSS, but a strong tell: transform-animation that changes
    // a translateX value repeatedly). As a proxy we look for the
    // class/id pattern AND absence of <button> children labelled
    // pause/stop — noise-tolerable because confidence=low.
    const carousels = [];
    for (const el of [...document.querySelectorAll('[class*="carousel" i], [class*="slider" i], [class*="slideshow" i]')]) {
        const r = el.getBoundingClientRect();
        if (r.width < 80 || r.height < 80) continue;
        const hasPause = !!el.querySelector(
            'button[aria-label*="pause" i], button[aria-label*="stop" i], ' +
            'button[title*="pause" i], button[title*="stop" i]'
        );
        if (hasPause) continue;
        carousels.push({
            selector: cssPath(el),
            html: el.outerHTML.slice(0, 200),
            class_name: (el.className || '').toString().slice(0, 80),
        });
    }

    return { marquee, infinite_animations, tiny_text, autoplay_media, carousels };
}
"""


def analyze(dom: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    for idx, m in enumerate(dom.get("marquee") or []):
        tag = m.get("tag", "")
        issues.append(
            make_issue(
                issue_id=f"visual-marquee-or-blink-{idx}",
                module="visual",
                rule="visual-marquee-or-blink",
                severity="serious",
                wcag=["2.2.2"],
                title=f"<{tag}> element is moving/blinking content with no pause control",
                description=(
                    f"<{tag}> is a deprecated element that moves or blinks indefinitely "
                    "without a built-in pause mechanism. WCAG 2.2.2 requires such content "
                    "to be pausable."
                ),
                selector=m.get("selector", ""),
                html_snippet=m.get("html", ""),
                details={"tag": tag},
                fix=(
                    "Replace with a pausable CSS animation, or a rotating component "
                    "with explicit pause/stop controls."
                ),
            )
        )

    for idx, a in enumerate(dom.get("infinite_animations") or []):
        dur = float(a.get("duration_s") or 0.0)
        if dur < MIN_ANIMATION_DURATION_S:
            # Very short micro-interactions (hover pulses, 0.3s fades) aren't
            # what WCAG 2.2.2 is about; skip them.
            continue
        issues.append(
            make_issue(
                issue_id=f"visual-infinite-animation-{idx}",
                module="visual",
                rule="visual-infinite-animation",
                severity="moderate",
                wcag=["2.2.2"],
                title=f"Element has infinite CSS animation ({dur}s cycle)",
                description=(
                    "Auto-starting animations longer than 5 seconds must have a pause, "
                    "stop, or hide control. Infinite animations always qualify."
                ),
                selector=a.get("selector", ""),
                html_snippet=a.get("html", ""),
                details={
                    "animation_name": a.get("animation_name", ""),
                    "duration_s": dur,
                    "iteration_count": "infinite",
                },
                fix=(
                    "Provide a visible pause control, respect prefers-reduced-motion, "
                    "or make the animation finite."
                ),
            )
        )

    for idx, t in enumerate(dom.get("tiny_text") or []):
        sz = float(t.get("font_size_px") or 0.0)
        issues.append(
            make_issue(
                issue_id=f"visual-tiny-text-{idx}",
                module="visual",
                rule="visual-tiny-text",
                severity="minor",
                wcag=["1.4.4"],
                title=f"Text rendered at only {sz}px",
                description=(
                    "Text smaller than 9px is hard to read for users with low vision "
                    "and often indicates a fixed pixel size that doesn't scale with "
                    "browser zoom."
                ),
                selector=t.get("selector", ""),
                html_snippet=t.get("html", ""),
                details={"font_size_px": sz, "tag": t.get("tag", "")},
                fix="Use at least 12px for body text, or a relative unit (rem/em/%) so it scales.",
            )
        )

    # Autoplaying audio/video (WCAG 1.4.2 Audio Control, level A).
    for idx, m in enumerate(dom.get("autoplay_media") or []):
        tag = m.get("tag", "")
        issues.append(
            make_issue(
                issue_id=f"visual-autoplay-sound-{idx}",
                module="visual",
                rule="visual-autoplay-sound",
                severity="serious",
                wcag=["1.4.2"],
                title=f"<{tag} autoplay> plays audio without a mute mechanism",
                description=(
                    f"<{tag}> has autoplay set but isn't muted. WCAG 1.4.2 "
                    "(level A) requires a mechanism to pause, stop, or mute "
                    "any audio that plays for more than 3 seconds. Screen-"
                    "reader users in particular need to silence this — it "
                    "competes with their SR output."
                ),
                selector=m.get("selector", ""),
                html_snippet=m.get("html", ""),
                details={"has_controls": m.get("has_controls")},
                fix=(
                    "Add `muted` to auto-played media, or drop `autoplay` "
                    "and start playback only after user interaction."
                ),
            )
        )

    # Carousels with no visible pause control (WCAG 2.2.2 Pause, Stop, Hide).
    for idx, c in enumerate(dom.get("carousels") or []):
        issues.append(
            make_issue(
                issue_id=f"visual-carousel-no-pause-{idx}",
                module="visual",
                rule="visual-carousel-no-pause",
                severity="serious",
                wcag=["2.2.2"],
                confidence="low",
                title="Carousel / slideshow has no visible pause control",
                description=(
                    "An element whose class or id suggests a carousel has "
                    "no <button> labelled pause/stop. If this carousel "
                    "auto-rotates, users with vestibular disorders or "
                    "screen-reader users who need stable content cannot "
                    "stop it. Confidence=low because we can't prove the "
                    "widget auto-rotates from static markup alone."
                ),
                selector=c.get("selector", ""),
                html_snippet=c.get("html", ""),
                details={"class_name": c.get("class_name", "")},
                fix=(
                    "If the widget auto-rotates, add a visible pause "
                    "button whose aria-label contains 'pause' or 'stop'. "
                    "If it only rotates on user interaction, ignore this "
                    "finding."
                ),
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
        "marquee_elements": len(dom.get("marquee") or []),
        "infinite_animations": len(dom.get("infinite_animations") or []),
        "tiny_text_elements": len(dom.get("tiny_text") or []),
    }
