"""The degraded-run contract: absence of evidence is never a defect.

The worst failure mode in this codebase is a confident false positive.
It happened in browse mode: NVDA captured no speech, the analyzer read
the empty transcript as "the page hid all of its text", and every clean
page reported 20 `serious` failures.

The general form of that bug is a module that cannot distinguish "I
measured, and the page is broken" from "I failed to measure". This file
holds every module to the contract:

    Given a page that yields nothing, a module emits NO findings.

Raising is acceptable — the orchestrator catches it and records a module
error, which is visible. Returning findings is not: that is invention.

A module that fails here should emit nothing and set `skip_reason`, the
way NVDAController.analyze_results and analyze_browse_mode do.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from audit import orchestrator


class _NullLocator:
    """Locator over an empty page: no elements, ever."""

    def count(self) -> int:
        return 0

    def all(self) -> list[Any]:
        return []

    def first(self):
        return self

    def nth(self, _i):
        return self

    def __getattr__(self, _name):
        return lambda *a, **k: None


class _NullAccessibility:
    def snapshot(self, **_kw):
        return None


class _NullPage:
    """A page whose every measurement comes back empty.

    Models a real, reachable situation: a blank document, a page whose
    JS failed, a navigation that resolved to nothing. None of these are
    accessibility defects.
    """

    def __init__(self, evaluate_value: Any):
        self._value = evaluate_value
        self.accessibility = _NullAccessibility()
        self.keyboard = self
        self.mouse = self

    def evaluate(self, *_a, **_k):
        return self._value

    def evaluate_handle(self, *_a, **_k):
        return None

    def locator(self, *_a, **_k):
        return _NullLocator()

    def query_selector_all(self, *_a, **_k):
        return []

    def query_selector(self, *_a, **_k):
        return None

    def content(self):
        return "<html><head></head><body></body></html>"

    def title(self):
        return ""

    @property
    def url(self):
        return "about:blank"

    def screenshot(self, **_kw):
        return b""

    def __getattr__(self, _name):
        # press/fill/click/wait_for_timeout/set_viewport_size/... all no-op.
        return lambda *a, **k: None


# The shapes an evaluate() can legitimately come back as when a page has
# nothing in it. Each is a separate scenario: a module may handle one and
# invent findings from another.
_EMPTY_SHAPES = [
    pytest.param([], id="empty-list"),
    pytest.param({}, id="empty-dict"),
    pytest.param(None, id="none"),
]


def _module_runners() -> list[tuple[str, Any]]:
    """Every audit module exposing the standard run(page, options)."""
    out = []
    for name, mod in vars(orchestrator).items():
        run = getattr(mod, "run", None)
        if not (inspect.ismodule(mod) and callable(run)):
            continue
        params = list(inspect.signature(run).parameters)
        # The standard shape is run(page, options=None). Modules taking a
        # different contract (yaml rules take a rule list) are exercised
        # by their own tests.
        if params[:1] != ["page"] or "options" not in params:
            continue
        out.append((name, mod))
    return sorted(out)


MODULES = _module_runners()


def test_the_module_inventory_is_not_empty():
    """Guard the guard: a broken discovery helper would make every test
    below vacuously pass."""
    assert len(MODULES) >= 25, f"only discovered {len(MODULES)} modules"


# Page-level "absence" rules: they assert that the DOCUMENT lacks
# something (a lang attribute, a title, a viewport meta, a
# prefers-reduced-motion query). On a genuinely blank document those
# findings are true, so firing here is defensible and they are pinned
# rather than fixed.
#
# They are still wrong when the probe FAILED rather than the page being
# empty, because the module cannot tell those apart. The real fix is
# probe provenance — every probe returning whether it ran, not just what
# it saw — which is a cross-cutting change to all 32 modules and belongs
# in its own piece of work. Until then this list is the honest record of
# where the gap is. Do not add to it without the same justification.
_PAGE_LEVEL_ABSENCE_ALLOWED = {
    ("structure", "structure-html-lang"),
    ("structure", "structure-title-missing"),
    ("structure", "structure-no-h1"),
    ("structure", "structure-no-main"),
    ("responsive", "responsive-viewport-meta-missing"),
    ("preferences", "preferences-no-reduced-motion-query"),
    ("preferences", "preferences-no-forced-colors-query"),
    # Fires only when the probe RAN and returned an empty candidate list
    # (a blank page really has no skip link). A None/non-list return now
    # yields a skip_reason instead — that was the failed-probe case.
    ("skiplinks", "skiplink-missing"),
}


def _issues_from(result: Any) -> list[Any]:
    if isinstance(result, dict):
        return result.get("issues") or []
    if isinstance(result, list):
        return result
    return []


@pytest.mark.parametrize("empty_value", _EMPTY_SHAPES)
@pytest.mark.parametrize("name,mod", MODULES, ids=[n for n, _ in MODULES])
def test_module_invents_no_findings_on_an_empty_page(name, mod, empty_value):
    page = _NullPage(empty_value)
    try:
        result = mod.run(page, {"skip_nvda": True})
    except Exception:
        # Acceptable: the orchestrator records a module error. Visible,
        # not invented. Hardening these into explicit skips is the
        # remaining half of the degraded-run work.
        return

    invented = [
        i for i in _issues_from(result)
        if (name, i.get("rule")) not in _PAGE_LEVEL_ABSENCE_ALLOWED
    ]
    assert not invented, (
        f"{name} invented {len(invented)} finding(s) from a page that measured "
        f"nothing: {[i.get('rule') for i in invented][:5]}. A module that cannot "
        f"measure must emit no findings and set skip_reason. If this is a "
        f"page-level absence rule that is true of a genuinely blank document, "
        f"add it to _PAGE_LEVEL_ABSENCE_ALLOWED with a justification."
    )


def test_the_allowlist_has_not_grown_silently():
    """The allowlist is a debt register, not a dumping ground.

    Every entry is a module that cannot distinguish a failed probe from an
    empty page. The number should fall as probe provenance lands; it must
    never rise without someone editing this assertion on purpose.
    """
    assert len(_PAGE_LEVEL_ABSENCE_ALLOWED) <= 8, (
        "more modules now invent findings from failed measurements; fix the "
        "module rather than extending the allowlist"
    )
