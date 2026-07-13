"""Screen reader module.

Path A (this file): Cross-platform analysis of Chromium's accessibility tree,
the same tree that screen readers consume via UIA/AT-SPI/IAccessible2 before
applying their own verbosity rules. Catches the canonical "silent element"
class of issues without needing a real screen reader running.

Path B (deferred): Real NVDA speech capture on a Windows worker. The
NVDAController class below is the placeholder entry point; see the project
design doc for the add-on and worker architecture.

Rules implemented (Path A):
- sr-silent-interactive     WCAG 4.1.2  critical   interactive-role node has no accessible name
- sr-empty-heading          WCAG 1.3.1  serious    heading with no accessible name
- sr-duplicate-landmark     WCAG 1.3.1  moderate   two or more landmarks share role and have no distinguishing name
- sr-dialog-no-name         WCAG 4.1.2  serious    dialog / alertdialog with no accessible name

Caveat: Playwright's accessibility.snapshot() does not expose a `focusable`
flag. Rules that depend on focus context (e.g. detecting a <div tabindex=0>
with no semantic role) live in the keyboard module instead, where we walk
focus directly.

Chromium's tree also differs from real NVDA output in verbosity rules,
browse-mode reading order, and punctuation. Real NVDA testing (Path B)
stacks additional rules on top when available.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from audit._issue import make_issue

log = logging.getLogger(__name__)


class NVDAUnavailableError(RuntimeError):
    """Raised when a Path B (real-NVDA) flow is requested but not available."""


INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "link",
        "checkbox",
        "radio",
        "textbox",
        "searchbox",
        "combobox",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "switch",
        "tab",
        "treeitem",
        "option",
        "slider",
        "spinbutton",
    }
)

# Landmark roles per ARIA spec (and HTML sectioning equivalents).
LANDMARK_ROLES = frozenset(
    {
        "banner",
        "complementary",
        "contentinfo",
        "form",
        "main",
        "navigation",
        "region",
        "search",
    }
)


def _walk(node: dict[str, Any]):
    """Depth-first iterator yielding every node in the a11y tree."""
    if not node:
        return
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        for child in reversed(n.get("children") or []):
            stack.append(child)


def _selector_hint(node: dict[str, Any]) -> str:
    """Best-effort human-readable hint for a tree node.

    The a11y tree doesn't carry CSS selectors, so we build a role+name hint.
    When the real NVDA pass lands it can correlate these by role+name or
    by injecting unique test IDs.
    """
    role = node.get("role", "?")
    name = (node.get("name") or "").strip()
    if name:
        return f'{role}[name="{name[:60]}"]'
    return role


def analyze(tree: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not tree:
        return []

    issues: list[dict[str, Any]] = []
    landmarks_by_role: dict[str, list[dict[str, Any]]] = {}

    nodes = list(_walk(tree))

    for node in nodes:
        role = (node.get("role") or "").lower()
        name = (node.get("name") or "").strip()
        disabled = bool(node.get("disabled"))

        # 1. interactive-role node with no accessible name
        if role in INTERACTIVE_ROLES and not name and not disabled:
            issues.append(
                make_issue(
                    issue_id=f"sr-silent-interactive-{_selector_hint(node)}",
                    module="screen_reader",
                    rule="sr-silent-interactive",
                    severity="critical",
                    wcag=["4.1.2"],
                    title=f'<{role}> has no accessible name',
                    description=(
                        "Chromium's accessibility tree exposes this element as a "
                        f"{role} but with no name. Screen readers will announce the "
                        "role alone (e.g. 'button') with nothing to identify it."
                    ),
                    selector=_selector_hint(node),
                    details={"role": role, "tree_name": name},
                    fix=(
                        "Add visible text, aria-label, aria-labelledby, or (for "
                        "inputs) a <label for> association."
                    ),
                )
            )

        # 3. empty heading (numbered in the rule list above)
        if role == "heading" and not name:
            issues.append(
                make_issue(
                    issue_id=f"sr-empty-heading-{_selector_hint(node)}",
                    module="screen_reader",
                    rule="sr-empty-heading",
                    severity="serious",
                    wcag=["1.3.1"],
                    title=f'Heading level {node.get("level","?")} has no text',
                    description=(
                        "Screen-reader users navigate by heading; empty headings appear "
                        "in that list as blank entries and break the document outline."
                    ),
                    selector=_selector_hint(node),
                    details={"level": node.get("level")},
                    fix="Remove the empty heading or add descriptive text content.",
                )
            )

        # 4. dialog with no accessible name
        if role in ("dialog", "alertdialog") and not name:
            issues.append(
                make_issue(
                    issue_id=f"sr-dialog-no-name-{_selector_hint(node)}",
                    module="screen_reader",
                    rule="sr-dialog-no-name",
                    severity="serious",
                    wcag=["4.1.2"],
                    title=f"<{role}> has no accessible name",
                    description=(
                        f"When the {role} opens, screen readers announce '{role}' with "
                        "no indication of what it is for."
                    ),
                    selector=_selector_hint(node),
                    details={"role": role},
                    fix="Add aria-label or aria-labelledby pointing to the dialog title.",
                )
            )

        # Collect landmarks for duplicate detection.
        if role in LANDMARK_ROLES:
            landmarks_by_role.setdefault(role, []).append(node)

    # 5. duplicate landmarks with no distinguishing names
    for role, lms in landmarks_by_role.items():
        if len(lms) < 2:
            continue
        names = [(n.get("name") or "").strip() for n in lms]
        # All empty or any two sharing the same name.
        duplicates_by_name: dict[str, list[dict[str, Any]]] = {}
        for lm, nm in zip(lms, names):
            duplicates_by_name.setdefault(nm, []).append(lm)
        for nm, group in duplicates_by_name.items():
            if len(group) < 2:
                continue
            # Report the second-plus occurrences (the first one is "the canonical").
            for dup_idx, lm in enumerate(group[1:], start=1):
                issues.append(
                    make_issue(
                        issue_id=f"sr-duplicate-landmark-{role}-{nm or '_unnamed'}-{dup_idx}",
                        module="screen_reader",
                        rule="sr-duplicate-landmark",
                        severity="moderate",
                        wcag=["1.3.1"],
                        title=(
                            f'Multiple <{role}> landmarks share '
                            + (f'name "{nm}"' if nm else "no accessible name")
                        ),
                        description=(
                            "Screen-reader users navigate landmarks via a list. Two "
                            f"{role} landmarks with the same (or empty) name are "
                            "indistinguishable in that list."
                        ),
                        selector=_selector_hint(lm),
                        details={"role": role, "shared_name": nm, "count": len(group)},
                        fix=(
                            f'Give each <{role}> a distinct aria-label '
                            '(e.g. aria-label="Primary" vs "Footer").'
                        ),
                    )
                )

    return issues


def _snapshot_via_legacy_api(page) -> dict[str, Any] | None:
    """Playwright <= 1.55 exposes page.accessibility.snapshot(). Returns None
    if the attribute is missing or the call fails."""
    acc = getattr(page, "accessibility", None)
    if acc is None:
        return None
    try:
        return acc.snapshot(interesting_only=False)
    except Exception as exc:
        log.debug("legacy accessibility snapshot failed: %s", exc)
        return None


def _snapshot_via_cdp(page) -> dict[str, Any] | None:
    """Build a Playwright-shape snapshot from Chrome DevTools Protocol.

    Works on every Playwright version (1.44 → latest). The CDP returns a
    flat list of AXNodes; we re-parent them into the nested tree shape
    the analyzer expects (role, name, level, children).

    CDP marks "uninteresting" nodes (purely presentational containers,
    anonymous generics) with `ignored=true`. We keep those in the raw
    map but skip them in the emitted tree, promoting their children up
    to the nearest non-ignored ancestor — otherwise a tree like
    `body > ignored_div > nav` would lose the nav entirely.
    """
    try:
        client = page.context.new_cdp_session(page)
        resp = client.send("Accessibility.getFullAXTree")
    except Exception as exc:
        log.debug("CDP Accessibility.getFullAXTree failed: %s", exc)
        return None

    nodes_raw: list[dict[str, Any]] = resp.get("nodes") or []
    if not nodes_raw:
        return None

    # Build two maps: id → raw (for traversal), id → node (for emission).
    raw_by_id: dict[str, dict[str, Any]] = {}
    node_by_id: dict[str, dict[str, Any]] = {}
    root_id: str | None = None

    for n in nodes_raw:
        nid = n["nodeId"]
        raw_by_id[nid] = n
        role_obj = n.get("role") or {}
        name_obj = n.get("name") or {}
        props = {p["name"]: p["value"] for p in (n.get("properties") or [])}
        node: dict[str, Any] = {
            "role": (role_obj.get("value") or "").lower(),
            "name": name_obj.get("value") or "",
            "children": [],
        }
        if "disabled" in props:
            node["disabled"] = bool((props.get("disabled") or {}).get("value"))
        if "level" in props:
            node["level"] = (props.get("level") or {}).get("value")
        node_by_id[nid] = node

        if role_obj.get("value") in ("RootWebArea", "WebArea") and root_id is None:
            root_id = nid

    # Fallback: if CDP didn't label a RootWebArea (rare), use the first
    # node with no parent pointer, which the walker relies on anyway.
    if root_id is None:
        # Find nodes that are never referenced as a childId.
        referenced: set[str] = set()
        for n in nodes_raw:
            for c in n.get("childIds") or []:
                referenced.add(c)
        for n in nodes_raw:
            if n["nodeId"] not in referenced:
                root_id = n["nodeId"]
                break
    if root_id is None:
        return None

    def _emit_children(node_id: str, depth: int = 0) -> list[dict[str, Any]]:
        # Defensive recursion cap for malformed trees.
        if depth > 200:
            return []
        out: list[dict[str, Any]] = []
        for cid in raw_by_id.get(node_id, {}).get("childIds") or []:
            craw = raw_by_id.get(cid)
            if craw is None:
                continue
            if craw.get("ignored"):
                # Skip this node in the emitted tree but pull up its children.
                out.extend(_emit_children(cid, depth + 1))
            else:
                cnode = node_by_id[cid]
                cnode["children"] = _emit_children(cid, depth + 1)
                out.append(cnode)
        return out

    root_node = node_by_id[root_id]
    root_node["children"] = _emit_children(root_id)
    return root_node


def run(page, options: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    """Snapshot the Chromium a11y tree and run the Path A analyzer.

    Tries the legacy `page.accessibility.snapshot()` first (available on
    Playwright <= 1.55), falls back to CDP `Accessibility.getFullAXTree`
    on newer versions which removed the sugar API.
    """
    start = time.time()
    tree = _snapshot_via_legacy_api(page)
    if tree is None:
        tree = _snapshot_via_cdp(page)

    if tree is None:
        return {
            "ran": False,
            "error": "accessibility snapshot unavailable via legacy API or CDP",
            "issues": [],
            "duration_seconds": round(time.time() - start, 3),
        }

    issues = analyze(tree)

    return {
        "ran": True,
        "issues": issues,
        "duration_seconds": round(time.time() - start, 3),
        "mode": "a11y-tree",
        "tree_nodes": sum(1 for _ in _walk(tree or {})),
        "note": (
            "Chromium a11y-tree analysis. Real NVDA speech capture (Path B) "
            "would stack additional rules when a Windows worker runs this job."
        ),
    }


# --------------------------------------------------------------------------
# Path B entry points — real NVDA on a Windows worker. Not implemented yet.
# --------------------------------------------------------------------------


# Default install locations for NVDA on Windows. Checked in order.
# NVDA's installed `nvda.exe` ships with a `requireAdministrator` manifest,
# so subprocess.Popen from an unelevated worker fails with WinError 740.
# The sibling `nvda_noUIAccess.exe` is the same engine without that manifest
# and without the UIAccess privilege — it launches cleanly from any shell
# and speaks/logs exactly the same way. Prefer it when present, fall back
# to nvda.exe for installations that don't ship the variant (older builds,
# portable copies).
_NVDA_DEFAULT_PATHS = (
    r"C:\Program Files (x86)\NVDA\nvda_noUIAccess.exe",
    r"C:\Program Files\NVDA\nvda_noUIAccess.exe",
    r"C:\Program Files (x86)\NVDA\nvda.exe",
    r"C:\Program Files\NVDA\nvda.exe",
)

# Scheduled Task name registered by scripts/setup_nvda_task.ps1.
# If the direct subprocess launch is refused with WinError 740 (the
# installed nvda.exe has a requireAdministrator manifest), we fall back
# to triggering this task, which was configured to run with highest
# privileges at setup time. This lets the unelevated worker launch NVDA
# on demand without a UAC prompt at audit time.
#
# AUTOAUDIT_NVDA_TASK flows directly into `schtasks /TN <task>`. Even
# though we always pass it as a separate argv element (no shell
# interpolation), schtasks itself rejects/parses unusual characters
# inconsistently across Windows versions, and exposing the task name
# to environment-controlled values is a soft injection surface. Hard
# constrain to a portable identifier alphabet.
_AUTOAUDIT_TASK_NAME_RE = __import__("re").compile(r"^[A-Za-z0-9_\-]{1,64}$")
_raw_task_name = os.environ.get("AUTOAUDIT_NVDA_TASK", "AutoauditNVDA")
if not _AUTOAUDIT_TASK_NAME_RE.match(_raw_task_name):
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "AUTOAUDIT_NVDA_TASK=%r is not a safe identifier "
        "([A-Za-z0-9_-]{1,64}); falling back to AutoauditNVDA",
        _raw_task_name,
    )
    _raw_task_name = "AutoauditNVDA"
AUTOAUDIT_TASK_NAME = _raw_task_name

# When NVDA is launched via the scheduled task, its `--log-file` arg was
# baked in at setup time. This constant must match the default in
# scripts/setup_nvda_task.ps1 (override with NVDA_LOG if you changed it).
_SCHEDULED_TASK_LOG = os.environ.get(
    "NVDA_LOG",
    str(Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Temp" / "autoaudit_nvda.log"),
)


def _find_nvda_executable() -> str | None:
    """Locate nvda.exe. Honours NVDA_EXE env var first, then default paths."""
    override = os.environ.get("NVDA_EXE")
    if override and Path(override).is_file():
        return override
    for candidate in _NVDA_DEFAULT_PATHS:
        if Path(candidate).is_file():
            return candidate
    return None


# A dedicated NVDA config directory lets us suppress the first-run
# Welcome dialog, the usage-stats prompt, and the update-check dialog —
# none of which can be turned off via CLI flags. NVDA reads these from
# its config.ini (ConfigObj format). By pointing NVDA at our own
# directory via `-c`, we never touch the user's real NVDA settings.
_AUTOAUDIT_NVDA_CONFIG_DIR = Path(
    os.environ.get(
        "AUTOAUDIT_NVDA_CONFIG",
        str(Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "autoaudit" / "nvda-config"),
    )
)

# NVDA reads these keys on startup. We deliberately omit schemaVersion
# so NVDA picks its own current value — hard-coding it breaks when the
# user has a newer NVDA than we anticipated (NVDA rejects the config
# and falls back to defaults, which re-shows the Welcome dialog).
#
# Section/key names below are taken from NVDA source:
#   general.showWelcomeDialogAtStartup   gui/startupDialogs.py
#   update.askedAllowUsageStats          updateCheck.py
#   update.autoCheck                     updateCheck.py
# ConfigObj requires a tab/space before each key inside a section; we
# use a single tab.
_QUIET_NVDA_CONFIG = """\
[general]
\tshowWelcomeDialogAtStartup = False
\tsaveConfigurationOnExit = False
[update]
\tautoCheck = False
\taskedAllowUsageStats = True
\tstartupNotification = False
[speechViewer]
\tshowSpeechViewerAtStartup = False
"""


def _ensure_quiet_nvda_config_dir() -> Path:
    """Prepare a dedicated NVDA config directory with dialogs disabled.

    Always rewrites nvda.ini before launch. NVDA rewrites the config
    on shutdown (inlining its own section/key ordering and dropping
    keys it considers defaults), so checking "is this my version?" is
    brittle — just reassert our canonical quiet config every run. The
    directory is autoaudit-owned; no user customisation belongs here.
    """
    cfg_dir = _AUTOAUDIT_NVDA_CONFIG_DIR
    try:
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "nvda.ini").write_text(_QUIET_NVDA_CONFIG, encoding="utf-8")
    except OSError as exc:
        log.warning("failed to prepare quiet NVDA config at %s: %s", cfg_dir, exc)
    return cfg_dir


# Matches the "speech" entries NVDA writes to its log at IO level.
# NVDA uses Python repr() to serialize speech sequences, so each element
# is either a quoted string or a CallbackCommand/LangChangeCommand object.
# Example (one line):
#   Speaking [LangChangeCommand ('en_US'), 'Submit', 'button']
_SPEAKING_LINE = re.compile(r"Speaking\s*\[(?P<payload>.*)\]")

# NVDA writes a header line before every log message. Example:
#   INFO - module.name (23:26:01.118) - MainThread (49080):
# The HH:MM:SS.mmm timestamp in parens is what we anchor on for
# time-window alignment with Playwright keystrokes.
_HEADER_TIMESTAMP = re.compile(
    r"\((?P<hh>\d{2}):(?P<mm>\d{2}):(?P<ss>\d{2})\.(?P<ms>\d{3})\)"
)


def _header_ts_to_epoch_today(hh: int, mm: int, ss: int, ms: int) -> float:
    """Convert an NVDA header time (no date) to a Unix epoch float,
    anchoring on today's date. Audits run in one calendar day so this
    is safe; the only pathological case is a run that straddles
    midnight, which we don't currently support."""
    import datetime as _dt
    today = _dt.date.today()
    dt = _dt.datetime.combine(
        today, _dt.time(hour=hh, minute=mm, second=ss, microsecond=ms * 1000)
    )
    return dt.timestamp()

# Python-repr string: either single- or double-quoted, with backslash
# escapes. We intentionally allow both because NVDA's log alternates
# based on which quote character appears inside the string itself.
_QUOTED = re.compile(
    r"""(?P<q>['"])(?P<body>(?:\\.|(?!(?P=q)).)*)(?P=q)"""
)

# Language-handler args embedded in LangChangeCommand look like
# BCP-47 locale codes. They appear as quoted strings but aren't
# speech, so filter them out of extracted utterances.
_LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:[_-][A-Za-z0-9]{2,4}){0,2}$")

# Roles/status words NVDA announces alongside an accessible name. These
# get concatenated into a single utterance; they're part of the final
# announcement, not noise.
_ALWAYS_KEEP = frozenset()  # reserved for future tuning


# Regexes for utterances that are almost certainly browser chrome /
# OS announcements, not page content. Stripping them before alignment
# keeps preamble drift from misclassifying the first tab stop as an
# sr-nvda-mismatch. We over-match a little on purpose: a false negative
# here (real announcement dropped) is much less disruptive than a false
# positive in the mismatch detector's output.
_PREAMBLE_PATTERNS = (
    re.compile(r"\bMicrosoft\s*(?:\u200b)?\s*Edge\b", re.IGNORECASE),
    re.compile(r"\bGoogle\s*Chrome\b", re.IGNORECASE),
    re.compile(r"\bMozilla\s*Firefox\b", re.IGNORECASE),
    re.compile(r"\bProfile\s*\d+\b", re.IGNORECASE),
    # Generic "document", "region", "web content" announcements that
    # land on the first interactive element. These are role
    # announcements without an accompanying name — we keep ones that
    # have a name (the name is the real utterance).
    re.compile(r"^\s*(window|region|document|web content|navigation)\s*$", re.IGNORECASE),
)


def _is_preamble(text: str) -> bool:
    """Heuristic: is this utterance browser/OS chrome?

    Called from analyze_results to filter per-utterance before we bin
    them into time windows. The list of patterns above is narrow by
    design — anything not explicitly matched is kept.
    """
    if not text:
        return True
    # Only treat a whole-utterance match as preamble. Partial matches
    # (e.g. "Submit button - Microsoft Edge" somewhere mid-string) are
    # risky to strip because they might contain the real label.
    return any(p.search(text) for p in _PREAMBLE_PATTERNS)


def _parse_log_speech_timed(log_text: str) -> list[tuple[float | None, str]]:
    """Extract (timestamp, utterance) tuples from an NVDA log excerpt.

    NVDA log format is TWO physical lines per entry:
        <LEVEL> - <module> (HH:MM:SS.mmm) - MainThread (PID):
        <content>
    We stream lines, remember the last header timestamp we saw, and
    attach it to the next `Speaking[...]` payload line.

    Returns float Unix-epoch-today seconds (None if a timestamp could
    not be parsed — typically the very first content line if capture
    started between a header and its body).
    """
    out: list[tuple[float | None, str]] = []
    pending_ts: float | None = None
    for line in log_text.splitlines():
        stripped = line.strip()
        hm = _HEADER_TIMESTAMP.search(stripped)
        if hm:
            try:
                pending_ts = _header_ts_to_epoch_today(
                    int(hm.group("hh")), int(hm.group("mm")),
                    int(hm.group("ss")), int(hm.group("ms")),
                )
            except ValueError:
                pending_ts = None
            continue
        sm = _SPEAKING_LINE.search(stripped)
        if not sm:
            continue
        fragments: list[str] = []
        for qmatch in _QUOTED.finditer(sm.group("payload")):
            body = qmatch.group("body")
            try:
                body = body.encode("utf-8").decode("unicode_escape")
            except UnicodeDecodeError:
                pass
            body = body.strip()
            if not body or _LOCALE_RE.match(body):
                continue
            fragments.append(body)
        if fragments:
            out.append((pending_ts, " ".join(fragments)))
    return out


def _parse_log_speech(log_text: str) -> list[str]:
    """Backward-compatible wrapper: drops timestamps, returns utterances
    only. Used by tests that don't care about alignment timing."""
    return [text for _ts, text in _parse_log_speech_timed(log_text)]


class NVDAController:
    """Path B controller: drives a real NVDA instance on Windows and captures
    spoken output by tailing NVDA's log file.

    Strategy:
      1. Locate nvda.exe (env var or default install path).
      2. Launch a dedicated NVDA instance with --log-level=DEBUG writing to a
         temp file we own. If NVDA is already running we attach to its
         configured log instead (non-destructive).
      3. Between start_capture() and stop_capture() we remember the log
         byte offsets so we can slice out just the speech that happened
         during the tab-walk.
      4. analyze_results() parses those log lines and compares NVDA's
         spoken text against each tab-stop's DOM accessible name, emitting
         sr-nvda-silent / sr-nvda-mismatch issues.

    All errors are surfaced as NVDAUnavailableError so the orchestrator
    can record "skipped" rather than crash-looping the whole audit.
    """

    STARTUP_WAIT_SECONDS = 8.0
    PER_STOP_SPEECH_WAIT = 0.35
    LOG_READ_TIMEOUT = 5.0

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._owns_nvda = False
        self._launched_via_task = False
        self._log_path: Path | None = None
        self._capture_start: int = 0
        self._capture_end: int | None = None

    # ------------------------------------------------------------------
    # Lifecycle

    def ensure_running(self) -> None:
        """Verify NVDA is installed and either attach to a running instance
        or launch our own. Raises NVDAUnavailableError on any failure."""
        if platform.system() != "Windows":
            raise NVDAUnavailableError(
                "NVDA is only available on Windows. Path A a11y-tree analysis "
                "runs regardless; see audit.screen_reader.run."
            )

        exe = _find_nvda_executable()
        if not exe:
            raise NVDAUnavailableError(
                "nvda.exe not found. Install NVDA from https://www.nvaccess.org/ "
                "or set NVDA_EXE to its path."
            )

        # Log location: our own temp file when we launch, default temp
        # location when NVDA is already running.
        default_log = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Temp" / "nvda.log"
        our_log = Path(tempfile.gettempdir()) / f"autoaudit_nvda_{os.getpid()}.log"

        if self._is_nvda_running():
            self._log_path = default_log
            self._owns_nvda = False
            log.info("attaching to running NVDA, reading log at %s", self._log_path)
            return

        # Launch a headless-ish NVDA we control.
        # -c points NVDA at an autoaudit-owned config directory where the
        # welcome dialog, usage-stats prompt, and update check are pre-
        # disabled. --minimal alone doesn't suppress those modal dialogs;
        # they're driven by config.ini flags NVDA reads on startup.
        cfg_dir = _ensure_quiet_nvda_config_dir()
        args = [
            exe,
            "-c", str(cfg_dir),
            "--log-level=12",  # DEBUG = 12, IO = 10 (NVDA's log_handler levels)
            f"--log-file={our_log}",
            "--minimal",       # no startup sound, no tray notification
            "--no-logging-lowlevel-input",  # keep log focused on speech
        ]
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - trusted path from env/default
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log_path = our_log
            self._owns_nvda = True
        except OSError as exc:
            # WinError 740 — "The requested operation requires elevation".
            # nvda.exe ships with a requireAdministrator manifest, so an
            # unelevated worker cannot launch it directly. Fall back to the
            # scheduled task registered by scripts/setup_nvda_task.ps1,
            # which was configured "Run with highest privileges" at setup
            # time. The task trigger (schtasks /Run) needs no elevation.
            winerr = getattr(exc, "winerror", None)
            if winerr == 740 and self._try_scheduled_task_launch():
                return
            raise NVDAUnavailableError(
                f"failed to launch NVDA: {exc}. "
                "On Windows, run scripts/setup_nvda_task.ps1 once as admin "
                "so the worker can trigger NVDA via a scheduled task, or "
                "re-run the worker as administrator."
            ) from exc

        # Wait for the log file to appear and NVDA to initialize. We poll
        # rather than sleep blindly because NVDA's startup time varies.
        deadline = time.time() + self.STARTUP_WAIT_SECONDS
        while time.time() < deadline:
            if our_log.exists() and our_log.stat().st_size > 0:
                # Give NVDA a moment to finish the startup chime so we
                # don't confuse its startup speech with real page speech.
                time.sleep(1.0)
                return
            time.sleep(0.2)

        # Didn't come up — try to clean up and fail cleanly.
        self._terminate_proc()
        raise NVDAUnavailableError(
            f"NVDA launched but produced no log within {self.STARTUP_WAIT_SECONDS}s"
        )

    def shutdown(self) -> None:
        """Terminate NVDA if we launched it. Safe to call multiple times."""
        if not self._owns_nvda:
            return
        if self._launched_via_task:
            # We triggered an elevated scheduled task; we can't signal the
            # elevated process directly from an unelevated worker, but
            # `schtasks /End` on our own user's task works without UAC.
            try:
                subprocess.run(  # noqa: S603,S607
                    ["schtasks", "/End", "/TN", AUTOAUDIT_TASK_NAME],
                    capture_output=True,
                    timeout=5,
                )
            except Exception as exc:  # pragma: no cover - best effort
                log.debug("schtasks /End failed: %s", exc)
            self._launched_via_task = False
        else:
            self._terminate_proc()
        self._owns_nvda = False

    def _terminate_proc(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        except Exception as exc:  # pragma: no cover - best effort
            log.debug("NVDA termination raised: %s", exc)
        finally:
            self._proc = None

    def _try_scheduled_task_launch(self) -> bool:
        """Trigger NVDA via the AutoauditNVDA scheduled task.

        Returns True on success, False if the task isn't registered or
        NVDA never shows up in the process table. Used as the fallback
        when a direct subprocess launch fails with WinError 740.
        """
        log_path = Path(_SCHEDULED_TASK_LOG)

        # Clear any stale log so `start_capture` isn't reading yesterday's
        # output. Failure here just means we'll see a longer preamble.
        try:
            if log_path.exists():
                log_path.unlink()
        except OSError:
            pass

        try:
            result = subprocess.run(  # noqa: S603,S607
                ["schtasks", "/Run", "/TN", AUTOAUDIT_TASK_NAME],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception as exc:
            log.debug("schtasks /Run failed to dispatch: %s", exc)
            return False

        if result.returncode != 0:
            # rc=1 typically means the task isn't registered; the stderr
            # string is the useful signal.
            log.warning(
                "schtasks /Run /TN %s failed (rc=%d): %s — run scripts/setup_nvda_task.ps1 as admin",
                AUTOAUDIT_TASK_NAME,
                result.returncode,
                (result.stderr or result.stdout or "").strip(),
            )
            return False

        # Poll for NVDA to appear in the process list AND for the log
        # file to become non-empty. schtasks returns immediately after
        # dispatch, so we have to wait for the task itself to start NVDA.
        deadline = time.time() + self.STARTUP_WAIT_SECONDS
        while time.time() < deadline:
            if self._is_nvda_running() and log_path.exists() and log_path.stat().st_size > 0:
                # Let NVDA finish its startup chime.
                time.sleep(1.0)
                self._log_path = log_path
                self._owns_nvda = True
                self._launched_via_task = True
                log.info("NVDA launched via scheduled task, log=%s", log_path)
                return True
            time.sleep(0.3)

        log.warning(
            "scheduled task %s dispatched but NVDA did not start within %.1fs",
            AUTOAUDIT_TASK_NAME,
            self.STARTUP_WAIT_SECONDS,
        )
        return False

    # ------------------------------------------------------------------
    # Capture

    def _is_nvda_running(self) -> bool:
        """Cheap check: is nvda.exe in the process list right now?"""
        try:
            out = subprocess.run(  # noqa: S603,S607 - tasklist is a trusted OS binary
                ["tasklist", "/FI", "IMAGENAME eq nvda.exe", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:  # pragma: no cover
            return False
        return "nvda.exe" in (out.stdout or "").lower()

    def start_capture(self) -> None:
        """Record the current log byte offset — subsequent lines are ours."""
        if self._log_path is None:
            raise NVDAUnavailableError("ensure_running() must be called first")
        try:
            self._capture_start = self._log_path.stat().st_size
        except FileNotFoundError:
            self._capture_start = 0
        self._capture_end = None

    def stop_capture(self) -> None:
        if self._log_path is None:
            raise NVDAUnavailableError("ensure_running() must be called first")
        try:
            self._capture_end = self._log_path.stat().st_size
        except FileNotFoundError:
            self._capture_end = self._capture_start

    def _read_captured_log(self) -> str:
        if self._log_path is None or self._capture_end is None:
            return ""
        try:
            with open(self._log_path, "rb") as fh:
                fh.seek(self._capture_start)
                chunk = fh.read(self._capture_end - self._capture_start)
        except OSError as exc:
            log.debug("NVDA log read failed: %s", exc)
            return ""
        # NVDA logs in utf-8; tolerate the occasional surrogate.
        return chunk.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # Analysis

    # Browse-mode configuration: how many Down-arrow presses at most,
    # how long to wait between each for speech to land. Browse mode
    # produces one utterance per "line" NVDA constructs, which is not
    # 1:1 with DOM elements, so we over-walk slightly (bigger cap than
    # tab walks) to give the read-all a chance to finish.
    BROWSE_MODE_MAX_STEPS = 80
    BROWSE_MODE_STEP_WAIT = 0.3

    def run_browse_mode(self, page) -> dict[str, Any]:
        """Walk the page in NVDA's browse mode (Down-arrow reading flow)
        and return the full transcript + a snapshot of visible DOM text.

        Tab walks (analyze_results) only cover FOCUSABLE elements, which
        is 5-10% of what a real SR user reads. Browse mode is how most
        SR reading happens: paragraphs, headings, list items, alt text
        on images — none of which are in the focus cycle.

        Strategy:
          1. Press Home to move NVDA's virtual cursor to the top of the
             document (this is how browse-mode users navigate to start).
          2. Press Down repeatedly until either:
             - NVDA stops producing new utterances for two consecutive
               steps (we reached the bottom of the read-all), OR
             - the step cap is hit (defensive bound).
          3. Capture a DOM snapshot: every visible text node with its
             `aria-hidden` ancestry status, for the analyzer to match
             against the transcript.

        Returns a dict matching the rest of this module's shape:
            {
              "ran": bool,
              "utterances": [str, ...],
              "visible_text_nodes": [{"text": str, "aria_hidden": bool}, ...],
              "log_bytes": int,
              "issues": [...],      # filled by analyze_browse_mode
            }
        """
        if self._log_path is None:
            raise NVDAUnavailableError("ensure_running() must be called first")

        # Sync the DOM snapshot first so we have the reference text
        # before any keystrokes could trigger focus-driven hydration.
        visible_nodes = page.evaluate(
            r"""
            () => {
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT, null, false
                );
                const out = [];
                while (walker.nextNode()) {
                    const n = walker.currentNode;
                    const txt = (n.nodeValue || '').trim();
                    if (!txt) continue;
                    // Walk up the parent chain to detect aria-hidden.
                    let p = n.parentElement;
                    let ariaHidden = false;
                    while (p) {
                        if (p.getAttribute && p.getAttribute('aria-hidden') === 'true') {
                            ariaHidden = true;
                            break;
                        }
                        p = p.parentElement;
                    }
                    // Skip text inside <script>, <style>, <template>.
                    const tag = (n.parentElement && n.parentElement.tagName || '').toLowerCase();
                    if (['script', 'style', 'template', 'noscript'].includes(tag)) continue;
                    out.push({ text: txt, aria_hidden: ariaHidden });
                }
                return out;
            }
            """
        )

        # Focus the body first so keystrokes go to the page, not to
        # whatever control happened to be focused.
        try:
            page.evaluate("() => { document.body.focus({ preventScroll: true }); }")
        except Exception:
            pass

        self.start_capture()
        # Home: move to top of document in browse mode.
        page.keyboard.press("Home")
        time.sleep(self.BROWSE_MODE_STEP_WAIT)

        last_size = 0
        stall = 0
        for _ in range(self.BROWSE_MODE_MAX_STEPS):
            page.keyboard.press("ArrowDown")
            time.sleep(self.BROWSE_MODE_STEP_WAIT)
            # Cheap progress probe: has the log grown?
            try:
                cur_size = self._log_path.stat().st_size
            except OSError:
                cur_size = last_size
            if cur_size == last_size:
                stall += 1
                if stall >= 2:
                    break
            else:
                stall = 0
            last_size = cur_size
        time.sleep(self.BROWSE_MODE_STEP_WAIT)
        self.stop_capture()

        captured = self._read_captured_log()
        timed = _parse_log_speech_timed(captured)
        utterances = [t for _ts, t in timed]

        return {
            "ran": True,
            "utterances": utterances,
            "visible_text_nodes": visible_nodes,
            "log_bytes": len(captured),
        }

    def analyze_results(self, tab_stops: list[dict[str, Any]]) -> dict[str, Any]:
        """Parse the captured NVDA log and compare with tab-stop names.

        Emits:
          - sr-nvda-silent: NVDA produced no speech at a focus stop where
            the DOM advertises an accessible name (the element is silent
            for screen-reader users even though Path A would pass it).
          - sr-nvda-mismatch: NVDA spoke something unrelated to the
            element's accessible name (heuristic below).

        Alignment strategy:
          - If tab stops carry a `press_time` field (wall-clock seconds
            from the keyboard walk), we use **time-window alignment**:
            utterances with an NVDA log timestamp in the window
            [stop.press_time, next_stop.press_time) belong to that stop.
            This is robust against preamble speech, missed utterances,
            and multi-utterance announcements.
          - Otherwise fall back to **sequence alignment** (zip stops and
            utterances by index). This is the legacy path; any real
            audit run goes through the timed path.
        """
        captured_log = self._read_captured_log()
        timed_all = _parse_log_speech_timed(captured_log)
        # Strip browser/OS chrome utterances BEFORE time-window binning.
        # Otherwise a "Microsoft Edge" announcement that lands 100ms
        # after the first Tab keystroke ends up paired with the first
        # real tab stop and fires a bogus sr-nvda-mismatch.
        timed = [(ts, t) for ts, t in timed_all if not _is_preamble(t)]
        preamble_dropped = len(timed_all) - len(timed)

        issues: list[dict[str, Any]] = []
        transcript: list[dict[str, Any]] = []

        # If NVDA produced zero utterances during the entire tab walk,
        # something was wrong at the OS level (NVDA sleeping, another
        # window owning focus, audio subsystem silenced). Firing a
        # "silent" rule for every stop would be noise. Skip per-stop
        # analysis entirely and return a single diagnostic result.
        if tab_stops and not timed:
            return {
                "ran": True,
                "issues": [],
                "tab_stops": len(tab_stops),
                "utterances_captured": 0,
                "nvda_transcript": [],
                "log_bytes": len(captured_log),
                "skipped_analysis": True,
                "skip_reason": (
                    "NVDA captured no speech during the tab walk. Focus "
                    "may have been owned by another window, or NVDA was "
                    "in sleep/muted state. Path A rules still apply."
                ),
            }

        have_times = bool(tab_stops) and all(s.get("press_time") for s in tab_stops)

        # Build the per-stop utterance lists.
        if have_times:
            stop_windows: list[list[str]] = [[] for _ in tab_stops]
            press_times = [s["press_time"] for s in tab_stops]
            # For each utterance, find the stop whose [press, next_press)
            # window contains it. Utterances before the first press are
            # preamble (page-load announcements) and intentionally dropped.
            for ts, text in timed:
                if ts is None:
                    continue
                if ts < press_times[0]:
                    continue
                for i in range(len(press_times)):
                    lo = press_times[i]
                    hi = press_times[i + 1] if i + 1 < len(press_times) else float("inf")
                    if lo <= ts < hi:
                        stop_windows[i].append(text)
                        break
            spoken_per_stop = [" ".join(ws).strip() for ws in stop_windows]
            # Focus-steal detection: if fewer than 30% of stops got any
            # speech AT ALL, NVDA was probably following a different
            # window's focus during most of the walk. Firing per-stop
            # mismatch rules in that regime would be noise; flip to a
            # single diagnostic issue and return early.
            populated = sum(1 for ws in stop_windows if ws)
            if populated and populated < max(1, len(stop_windows) // 3):
                return {
                    "ran": True,
                    "issues": [],
                    "tab_stops": len(tab_stops),
                    "utterances_captured": len(timed),
                    "preamble_utterances_dropped": preamble_dropped,
                    "nvda_transcript": [],
                    "log_bytes": len(captured_log),
                    "skipped_analysis": True,
                    "skip_reason": (
                        f"NVDA only spoke for {populated}/{len(stop_windows)} "
                        "tab stops. A different window likely owned focus "
                        "during most of the walk. Path A rules still apply."
                    ),
                }
        else:
            # Legacy sequence alignment. Zip trims the tail on mismatch.
            utterances_plain = [t for _ts, t in timed]
            spoken_per_stop = list(utterances_plain[: len(tab_stops)])
            # Pad with "" so enumerate below sees every stop.
            spoken_per_stop += [""] * (len(tab_stops) - len(spoken_per_stop))

        for idx, (stop, spoken) in enumerate(zip(tab_stops, spoken_per_stop)):
            dom_name = (stop.get("accessible_name") or "").strip()
            spoken_norm = (spoken or "").strip()
            transcript.append(
                {
                    "index": idx,
                    "selector": stop.get("selector"),
                    "dom_name": dom_name,
                    "nvda_spoken": spoken_norm,
                }
            )
            if dom_name and not spoken_norm:
                issues.append(
                    make_issue(
                        issue_id=f"sr-nvda-silent-{idx}",
                        module="screen_reader",
                        rule="sr-nvda-silent",
                        severity="serious",
                        wcag=["4.1.2"],
                        title="NVDA was silent at a focusable element",
                        description=(
                            "The accessibility tree exposes a name for this element, "
                            "but NVDA did not announce anything when it received focus. "
                            "This usually means a role/name combination Chromium "
                            "reports but NVDA's heuristics reject."
                        ),
                        selector=stop.get("selector"),
                        html_snippet=stop.get("html"),
                        details={"dom_name": dom_name, "tab_index": idx + 1},
                        fix=(
                            "Use a native control (<button>, <a href>) or pair "
                            "role= with aria-label on the same element."
                        ),
                    )
                )
                continue
            if dom_name and spoken_norm and _semantic_divergence(dom_name, spoken_norm):
                issues.append(
                    make_issue(
                        issue_id=f"sr-nvda-mismatch-{idx}",
                        module="screen_reader",
                        rule="sr-nvda-mismatch",
                        severity="moderate",
                        wcag=["2.5.3"],
                        title="NVDA announces a different name than the DOM",
                        description=(
                            "NVDA spoke text that doesn't overlap the element's "
                            "accessible name. Speech-input users who say the visible "
                            "label cannot activate this control."
                        ),
                        selector=stop.get("selector"),
                        html_snippet=stop.get("html"),
                        details={
                            "dom_name": dom_name,
                            "nvda_spoken": spoken_norm,
                            "tab_index": idx + 1,
                        },
                        fix=(
                            "Make the accessible name start with the visible label. "
                            "Tools commonly trip on aria-label overriding visible text."
                        ),
                    )
                )

        return {
            "ran": True,
            "issues": issues,
            "tab_stops": len(tab_stops),
            "utterances_captured": len(timed),
            "preamble_utterances_dropped": preamble_dropped,
            "nvda_transcript": transcript,
            "log_bytes": len(captured_log),
        }


def analyze_browse_mode(browse_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare NVDA's browse-mode transcript to the page's visible DOM
    text and emit issues for what slipped through in either direction.

    Two rule families:

    - `sr-browse-skipped-text` (serious, WCAG 1.3.1 / 1.3.2):
      Visible, non-aria-hidden text that NVDA never spoke during the
      read-all. Typical cause: content rendered with CSS that removes
      it from the accessibility tree (e.g. custom fonts via icon-font
      pseudo-elements, or `<p role="presentation">`).

    - `sr-browse-decorative-noise` (moderate, WCAG 1.3.1):
      Text wrapped in `aria-hidden="true"` that NVDA nonetheless
      announced. Browsers generally respect aria-hidden, but deeply
      nested cases (fallback text inside a button with aria-label)
      still leak through some SR heuristics.

    Matching is substring-based, case-insensitive, over normalized
    whitespace. A DOM node counts as "spoken" if ANY utterance
    contains its normalized text as a substring — this tolerates
    NVDA's habit of prefixing role/state ("heading level 2, …").
    """
    issues: list[dict[str, Any]] = []
    utterances: list[str] = browse_result.get("utterances") or []
    nodes: list[dict[str, Any]] = browse_result.get("visible_text_nodes") or []

    if not nodes:
        return issues

    # Build one big lowercase corpus of everything NVDA said. Using a
    # single string (rather than scanning every utterance per node)
    # keeps the complexity at O(nodes) instead of O(nodes * utterances).
    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").lower()).strip()

    spoken_corpus = _norm(" ".join(utterances))

    # Ignore very short bits of text — single characters, bullets,
    # separators. They're rarely meaningful content and are the most
    # likely false-positive source for "SR skipped this".
    _MIN_TEXT_LEN = 4
    # Cap how many skipped-text issues we emit per page. A wildly
    # broken page could otherwise produce hundreds of rows of noise.
    _MAX_SKIPPED_ISSUES = 20

    skipped_count = 0
    for node in nodes:
        txt = (node.get("text") or "").strip()
        if len(txt) < _MIN_TEXT_LEN:
            continue
        norm_txt = _norm(txt)
        if node.get("aria_hidden"):
            if norm_txt in spoken_corpus:
                issues.append(
                    make_issue(
                        issue_id=f"sr-browse-decorative-noise-{hash(txt) & 0xFFFFFF:x}",
                        module="screen_reader",
                        rule="sr-browse-decorative-noise",
                        severity="moderate",
                        wcag=["1.3.1"],
                        title="aria-hidden text was announced by NVDA",
                        description=(
                            "This text is marked aria-hidden=\"true\" to hide "
                            "it from assistive tech, but NVDA announced it in "
                            "browse mode anyway. Either the hiding is wrong "
                            "or an ancestor attribute is being overridden."
                        ),
                        details={"text": txt[:200]},
                        fix=(
                            "Check for role= or aria-label= on the same or "
                            "child element that re-exposes this content. "
                            "If the text is decorative, prefer moving it to "
                            "CSS content: property."
                        ),
                    )
                )
            continue
        # Non-hidden text: should have been spoken.
        if norm_txt not in spoken_corpus:
            if skipped_count >= _MAX_SKIPPED_ISSUES:
                break
            skipped_count += 1
            issues.append(
                make_issue(
                    issue_id=f"sr-browse-skipped-text-{hash(txt) & 0xFFFFFF:x}",
                    module="screen_reader",
                    rule="sr-browse-skipped-text",
                    severity="serious",
                    wcag=["1.3.1", "1.3.2"],
                    title="Visible text was not read aloud in browse mode",
                    description=(
                        "This text is visible on the page but NVDA's "
                        "read-all (browse mode) skipped it. Screen-reader "
                        "users reading the page top-to-bottom will never "
                        "encounter this content."
                    ),
                    details={"text": txt[:200]},
                    fix=(
                        "Ensure the text is in the accessibility tree: "
                        "avoid rendering text via CSS pseudo-elements or "
                        "canvas, and check that no ancestor has "
                        "aria-hidden=\"true\" or role=\"presentation\"."
                    ),
                )
            )

    return issues


def _normalize_for_2_5_3(s: str) -> str:
    """Normalize strings for WCAG 2.5.3 "Label in Name" comparison.

    Rules per the Understanding doc:
      - case-insensitive
      - whitespace-normalized (internal whitespace collapsed, leading /
        trailing stripped)
      - leading/trailing punctuation stripped (so a label "Save *" and
        an accessible name "Save the document" still match on "save")
    """
    # Keep letters, digits, and internal whitespace; drop other
    # punctuation entirely. This is the normalization axe-core uses.
    lowered = s.lower()
    stripped = re.sub(r"[^\w\s]+", " ", lowered, flags=re.UNICODE)
    return re.sub(r"\s+", " ", stripped).strip()


def analyze_label_in_name(tab_stops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """WCAG 2.5.3 Label in Name (level A).

    For every focusable control that has BOTH visible text AND a
    computed accessible name, the accessible name must contain the
    visible text (normalized). Otherwise speech-input users who voice
    the visible label cannot activate the control.

    This is Path A: the rule runs off the keyboard walk's focus probe,
    which captures both the visible text (element innerText / label
    innerText) and the accessible name (computed the same way browsers
    compute it for AT).

    Emits `sr-label-in-name` (serious, WCAG 2.5.3 / A).
    """
    issues: list[dict[str, Any]] = []

    for idx, stop in enumerate(tab_stops):
        visible = (stop.get("visible_text") or "").strip()
        acc = (stop.get("accessible_name") or "").strip()
        if not visible or not acc:
            # No visible text (icon-only control) or no accessible name
            # at all — the latter is a different violation (4.1.2),
            # already caught by sr-silent-interactive / keyboard-no-
            # accessible-name, so we don't double-emit here.
            continue

        vn = _normalize_for_2_5_3(visible)
        an = _normalize_for_2_5_3(acc)
        if not vn:
            continue
        if vn in an:
            # Accessible name contains the visible text — passes 2.5.3.
            continue

        issues.append(
            make_issue(
                issue_id=f"sr-label-in-name-{stop.get('selector') or idx}",
                module="screen_reader",
                rule="sr-label-in-name",
                severity="serious",
                wcag=["2.5.3"],
                title="Accessible name does not contain the visible label",
                description=(
                    f"The control displays '{visible[:60]}' but its accessible "
                    f"name is '{acc[:60]}'. Speech-input users voicing the "
                    "visible label cannot activate the control, and screen "
                    "readers announce a name different from what sighted "
                    "users see (WCAG 2.5.3 Label in Name, level A)."
                ),
                selector=stop.get("selector"),
                html_snippet=stop.get("html"),
                details={
                    "visible_text": visible,
                    "accessible_name": acc,
                    "tab_index": idx + 1,
                },
                fix=(
                    "Make the accessible name start with (or contain) the "
                    "visible text verbatim. Typical cause: an aria-label "
                    "that overrides the visible button text with different "
                    "wording — use aria-labelledby pointing to the visible "
                    "element, or remove the aria-label entirely."
                ),
            )
        )

    return issues


def _semantic_divergence(dom_name: str, spoken: str) -> bool:
    """Return True if `spoken` appears unrelated to `dom_name`.

    Heuristic: if either is a substring of the other, they match. Otherwise
    compute the Jaccard token overlap and call it a mismatch below 0.3.
    Intentionally conservative — false positives here are annoying.
    """
    a, b = dom_name.lower().strip(), spoken.lower().strip()
    if not a or not b:
        return False
    if a in b or b in a:
        return False
    ta = set(w for w in re.split(r"\W+", a) if w)
    tb = set(w for w in re.split(r"\W+", b) if w)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(1, len(ta | tb))
    return overlap < 0.3
