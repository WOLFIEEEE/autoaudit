"""Plugin discovery via setuptools entry points.

Lets third-party Python packages contribute audit modules without
forking. A plugin package declares one or more entry points under
the `autoaudit.modules` group:

    # pyproject.toml
    [project.entry-points."autoaudit.modules"]
    my-team-rules = "my_team_rules:plugin"

The named callable returns a `Plugin` dict:

    def plugin():
        return {
            "name": "my-team-rules",
            "version": "1.0.0",
            "rules": {
                "my-team-button-class": "1.0.0",
                "my-team-color-token": "1.0.0",
            },
            "run": run_module,        # def run(page, options) -> {ran, issues, ...}
        }

The orchestrator calls `discover()` once at startup, registers each
plugin's rule versions with the registry (so `rule_set_hash` stays
honest), and runs the plugin's module like any built-in.

Why entry points (rather than a config file): they ship with the
package metadata, are sandboxed by Python's import machinery, and are
discoverable via `pip list`/`pip show`. A buyer auditing your CI run
can verify which plugin packages contributed rules.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from audit import rule_versions

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "autoaudit.modules"


# Cache the discovery result. Plugin discovery walks installed
# packages; doing it on every audit is wasteful, and discovery is
# inherently process-lifecycle (you don't `pip install` mid-run).
_DISCOVERED: list[dict[str, Any]] | None = None


def discover(force: bool = False) -> list[dict[str, Any]]:
    """Return every registered plugin descriptor.

    Each descriptor is the plugin's `plugin()` return value plus an
    `_origin` key with the source distribution name, so the rule
    catalog can attribute findings back to the package that shipped
    them.

    Failed-to-load plugins are logged and skipped — we never crash
    the audit because a third-party package is broken.
    """
    global _DISCOVERED
    if _DISCOVERED is not None and not force:
        return _DISCOVERED

    plugins: list[dict[str, Any]] = []
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover - Py<3.8
        log.warning("importlib.metadata unavailable; plugin loading skipped")
        _DISCOVERED = plugins
        return plugins

    try:
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:
        # Py 3.9 returned a dict; fall back.
        eps = entry_points().get(ENTRY_POINT_GROUP, [])  # type: ignore[union-attr]

    for ep in eps:
        try:
            factory = ep.load()
        except Exception as exc:
            log.warning("plugin %s failed to load: %s: %s",
                        ep.name, type(exc).__name__, exc)
            continue
        try:
            plugin = factory() if callable(factory) else factory
        except Exception as exc:
            log.warning("plugin %s factory raised %s: %s",
                        ep.name, type(exc).__name__, exc)
            continue
        if not _validate(plugin, ep.name):
            continue
        plugin = dict(plugin)
        plugin["_origin"] = getattr(ep, "dist", None) and ep.dist.name or ep.name
        plugins.append(plugin)
        # Register the plugin's rule versions so the orchestrator's
        # rule_set_hash includes them. Without this, two CI runs that
        # differ only in a plugin upgrade would compute the same hash —
        # silently breaking the reproducibility claim.
        for rule_id, version in (plugin.get("rules") or {}).items():
            rule_versions.register(rule_id, str(version))
        log.info("loaded plugin %s (%d rules)",
                 plugin.get("name", ep.name), len(plugin.get("rules") or {}))
    _DISCOVERED = plugins
    return plugins


def _validate(plugin: Any, ep_name: str) -> bool:
    """Sanity-check a plugin descriptor."""
    if not isinstance(plugin, dict):
        log.warning("plugin %s: factory must return a dict, got %s",
                    ep_name, type(plugin).__name__)
        return False
    if not isinstance(plugin.get("name"), str):
        log.warning("plugin %s: missing string `name`", ep_name)
        return False
    if not callable(plugin.get("run")):
        log.warning("plugin %s: missing callable `run(page, options)`", ep_name)
        return False
    rules = plugin.get("rules") or {}
    if not isinstance(rules, dict):
        log.warning("plugin %s: `rules` must be a dict[str, str]", ep_name)
        return False
    for rid, ver in rules.items():
        if not isinstance(rid, str) or not isinstance(ver, str):
            log.warning("plugin %s: rule entries must be (str, str)", ep_name)
            return False
    return True


def run_plugins(page, options: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Run every loaded plugin's `run()` and return their module
    results keyed by `<origin>.<plugin name>`. The orchestrator merges
    these into `self.results` so plugin issues participate in dedupe,
    grouping, and the report just like built-in modules.
    """
    results: dict[str, dict[str, Any]] = {}
    for plugin in discover():
        run_fn: Callable[..., dict[str, Any]] = plugin["run"]
        key = f"plugin.{plugin.get('name', '?')}"
        start = time.time()
        try:
            results[key] = run_fn(page, options) or {}
        except Exception as exc:
            log.exception("plugin %s raised", plugin.get("name"))
            results[key] = {
                "ran": False,
                "error": f"{type(exc).__name__}: {exc}",
                "issues": [],
                "duration_seconds": round(time.time() - start, 3),
            }
    return results
