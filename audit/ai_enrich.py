"""OpenRouter-powered enrichment of audit findings.

Each audit issue already carries the mechanical facts (rule id, WCAG
criteria, selector, severity, level). Stakeholders also want
higher-context fields that are hard to generate from rules alone:

  - `location_guide`     — human-language "where to find this" with
                           navigation cues ("scroll to the pricing
                           section, first form field").
  - `reproduction_steps` — numbered steps to reproduce the failure
                           using the keyboard or an SR.
  - `recommendation`     — an actionable fix, phrased for a dev who
                           owns this code (as opposed to our generic
                           `fix_suggestion` template string).
  - `user_impact`        — the groups most affected (SR users, low-
                           vision users, motor-impaired users, etc.)
                           with a short severity-of-impact note.

We call OpenRouter's chat-completions API in batches (one request per
N issues) with a low-cost default model. The module fails CLOSED — if
no API key is set, enrichment is skipped and the audit result is
returned unchanged, so this never blocks a run.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterable

log = logging.getLogger(__name__)

# Default model. Pick something cheap + fast with good JSON fidelity.
# Can be overridden via env so different deployments can swap without
# code changes.
DEFAULT_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")

# Batch size. Too large → one slow issue delays everything; too small →
# per-request overhead dominates. 8 is a reasonable middle for a page
# with 50–100 issues.
DEFAULT_BATCH = int(os.environ.get("OPENROUTER_BATCH_SIZE", "8"))

# Request timeout per call. OpenRouter sits behind provider APIs, some
# of which slow to 20-30s under load; 60s keeps us patient without
# hanging a CI pipeline.
REQUEST_TIMEOUT_S = float(os.environ.get("OPENROUTER_TIMEOUT", "60"))

# Keep the system prompt as a module constant so it's both visible and
# easy to tune without rummaging through request-building code.
_SYSTEM_PROMPT_EN = (
    "You are an accessibility expert writing findings for a WCAG 2.2 "
    "compliance report. For each issue below, produce four enriched "
    "fields: location_guide, reproduction_steps, recommendation, and "
    "user_impact. Keep each field short (2-4 sentences max). Never "
    "invent selectors, URLs, or WCAG criteria — use only what is given. "
    "Respond with a single JSON object whose keys are the provided "
    "issue ids and whose values are objects with those four fields."
)


def _system_prompt_for(language: str) -> str:
    """Localize the enrichment prompt. Uses full-language names (not
    BCP-47 codes) so the model's instruction-following is reliable
    across non-dominant locales. English stays the canonical text; the
    localized variant just appends an instruction to write in that
    language."""
    lang = (language or "").strip().lower()
    if not lang or lang in ("en", "en-us", "english"):
        return _SYSTEM_PROMPT_EN
    # Map BCP-47 codes to full names where common; otherwise pass
    # through the raw value and trust the model.
    name_map = {
        "es": "Spanish", "es-es": "Spanish",
        "fr": "French", "fr-fr": "French",
        "de": "German", "de-de": "German",
        "ja": "Japanese", "ja-jp": "Japanese",
        "zh": "Chinese", "zh-cn": "Chinese",
        "pt": "Portuguese", "pt-br": "Portuguese",
        "it": "Italian",
        "nl": "Dutch",
        "ko": "Korean",
        "hi": "Hindi",
        "ar": "Arabic",
    }
    lang_name = name_map.get(lang, language)
    return (
        _SYSTEM_PROMPT_EN
        + f"\n\nIMPORTANT: write every field's value in {lang_name}. "
        "Keep JSON keys and WCAG SC numbers in English."
    )


# --------------------------------------------------------------------
# Public API


def enrich_issues(
    issues: list[dict[str, Any]],
    *,
    api_key: str | None = None,
    model: str | None = None,
    batch_size: int | None = None,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """Return a new list of issues with enrichment fields added.

    Non-destructive: the original issue dicts are not mutated, so
    callers can persist the raw audit and enriched version side by
    side. If enrichment is unavailable (no key, request failure) the
    issues are returned unchanged.
    """
    api_key = (api_key or os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        log.info("OPENROUTER_API_KEY not set; skipping AI enrichment")
        # Still annotate so downstream consumers can distinguish
        # "no enrichment attempted" from "enrichment returned empty".
        return [dict(i, ai_enriched=False) for i in issues]

    model = model or DEFAULT_MODEL
    batch = int(batch_size or DEFAULT_BATCH)
    language = language or os.environ.get("OPENROUTER_LANGUAGE") or "en"

    enriched_by_id: dict[str, dict[str, Any]] = {}
    for group in _chunk(issues, batch):
        try:
            enriched_by_id.update(
                _call_openrouter(group, api_key=api_key, model=model, language=language)
            )
        except Exception as exc:  # one bad batch shouldn't sink the rest
            log.warning("enrichment batch failed: %s", exc)

    out: list[dict[str, Any]] = []
    for issue in issues:
        extras = enriched_by_id.get(issue.get("id") or "", {})
        merged = dict(issue)
        if extras:
            # Namespace the enrichment under a single key so callers
            # can distinguish rule-authored vs AI-authored fields.
            merged["ai"] = {
                "location_guide": extras.get("location_guide", ""),
                "reproduction_steps": extras.get("reproduction_steps", ""),
                "recommendation": extras.get("recommendation", ""),
                "user_impact": extras.get("user_impact", ""),
                "model": model,
            }
            merged["ai_enriched"] = True
        else:
            merged["ai_enriched"] = False
        out.append(merged)
    return out


# --------------------------------------------------------------------
# Internals


def _chunk(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), max(size, 1)):
        yield items[i : i + size]


def _summarize_issue_for_prompt(issue: dict[str, Any]) -> dict[str, Any]:
    """Strip an issue down to the fields the model actually needs.

    We don't ship the full issue dict — it has `details` subfields that
    balloon token count without helping the enrichment. A compact
    summary keeps each batch within the model's context comfortably.
    """
    el = issue.get("element") or {}
    return {
        "id": issue.get("id"),
        "rule": issue.get("rule"),
        "title": issue.get("title"),
        "description": issue.get("description"),
        "severity": issue.get("severity"),
        "level": issue.get("level"),
        "wcag_criteria": issue.get("wcag_criteria"),
        "principle": issue.get("principle"),
        "selector": el.get("selector"),
        "html_snippet": (el.get("html_snippet") or "")[:300],
        "fix_hint": issue.get("fix_suggestion"),
    }


def _call_openrouter(
    issues: list[dict[str, Any]],
    *,
    api_key: str,
    model: str,
    language: str = "en",
) -> dict[str, dict[str, str]]:
    """One HTTP call → parsed {issue_id: {field: str, ...}} dict."""
    import httpx  # local import: optional dependency in some deployments

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt_for(language)},
            {
                "role": "user",
                "content": (
                    "Here are the issues. Return a JSON object keyed by issue id.\n\n"
                    + json.dumps([_summarize_issue_for_prompt(i) for i in issues], indent=2)
                ),
            },
        ],
        # Force JSON output so we don't have to fish a blob out of
        # prose. OpenRouter passes this through to providers that
        # support it (OpenAI-compatible ones — our default gpt-4o-mini
        # does).
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # OpenRouter asks for these headers so the request shows up
        # with attribution in their dashboard. Failing to send them
        # is not fatal but is a trivial courtesy.
        "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", "https://github.com/autoaudit"),
        "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "Autoaudit"),
    }
    with httpx.Client(timeout=REQUEST_TIMEOUT_S) as client:
        resp = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"unexpected OpenRouter response shape: {exc}") from exc

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"model returned non-JSON content: {exc}") from exc

    if not isinstance(parsed, dict):
        raise RuntimeError("model returned a non-object at top level")

    # Sanity: drop any entry whose value isn't the shape we asked for.
    clean: dict[str, dict[str, str]] = {}
    for k, v in parsed.items():
        if not isinstance(v, dict):
            continue
        clean[k] = {
            "location_guide": str(v.get("location_guide") or "").strip(),
            "reproduction_steps": str(v.get("reproduction_steps") or "").strip(),
            "recommendation": str(v.get("recommendation") or "").strip(),
            "user_impact": str(v.get("user_impact") or "").strip(),
        }
    return clean
