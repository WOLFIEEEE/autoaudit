"""Tkinter GUI launcher for autoaudit.

    python scripts/gui.py

Enter a URL, tick a few options, click Run. The audit runs in a
background thread so the window stays responsive. When it finishes
you get:

  - A summary card in the window (score, grade, severity counts,
    conformance badges, top blockers).
  - "Open full report" — renders the existing HTML report template to
    a temp file and opens it in your default browser.
  - "Save JSON" — writes the raw result dict to a file you pick.

Runs the orchestrator in-process (no server required), so behavior
matches `scripts/audit_cli.py`. Zero new dependencies — Tkinter ships
with Python and Jinja2 is already used by the HTML report.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

# Make `audit.*` imports work when running this script directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit.orchestrator import AuditOrchestrator  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "templates"


SEVERITY_COLORS = {
    "critical": "#b00020",
    "serious": "#d07000",
    "moderate": "#a57c00",
    "minor": "#5a5a5a",
}
GRADE_COLORS = {
    "A": "#1a7f37", "B": "#1a7f37",
    "C": "#a57c00",
    "D": "#d07000",
    "F": "#b00020",
}


class AuditApp:
    """Main application window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("autoaudit — accessibility audit")
        self.root.geometry("960x720")
        self.root.minsize(720, 560)

        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._result: dict[str, Any] | None = None

        self._build_layout()

    # ------------------------------------------------------------------
    # UI construction.

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        # URL row.
        url_row = ttk.Frame(outer)
        url_row.pack(fill="x")
        ttk.Label(url_row, text="URL", width=6).pack(side="left")
        self.url_var = tk.StringVar(value="https://example.com")
        self.url_entry = ttk.Entry(url_row, textvariable=self.url_var)
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(4, 8))
        self.run_btn = ttk.Button(url_row, text="Run audit", command=self._start_audit)
        self.run_btn.pack(side="left")

        # Options row.
        opts = ttk.LabelFrame(outer, text="Options", padding=8)
        opts.pack(fill="x", pady=(10, 0))

        self.headless_var = tk.BooleanVar(value=True)
        self.screenshots_var = tk.BooleanVar(value=True)
        self.vlm_var = tk.BooleanVar(value=False)
        self.skip_nvda_var = tk.BooleanVar(value=False)

        ttk.Checkbutton(opts, text="Headless browser", variable=self.headless_var).grid(
            row=0, column=0, sticky="w", padx=4, pady=2,
        )
        ttk.Checkbutton(
            opts, text="Per-issue screenshots", variable=self.screenshots_var,
        ).grid(row=0, column=1, sticky="w", padx=4, pady=2)
        ttk.Checkbutton(
            opts,
            text="VLM semantic checks (needs OPENROUTER_API_KEY)",
            variable=self.vlm_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=4, pady=2)
        ttk.Checkbutton(
            opts, text="Skip NVDA (Path B)", variable=self.skip_nvda_var,
        ).grid(row=2, column=0, sticky="w", padx=4, pady=2)

        # Progress + status.
        status_row = ttk.Frame(outer)
        status_row.pack(fill="x", pady=(10, 0))
        self.progress = ttk.Progressbar(status_row, mode="indeterminate", length=260)
        self.progress.pack(side="left", padx=(0, 10))
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(status_row, textvariable=self.status_var, foreground="#555").pack(
            side="left",
        )

        # Results panel — scrollable canvas so long result sets scroll.
        body = ttk.LabelFrame(outer, text="Results", padding=4)
        body.pack(fill="both", expand=True, pady=(10, 0))
        self.canvas = tk.Canvas(body, borderwidth=0, highlightthickness=0)
        self.scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        # Frame inside canvas for the actual result widgets.
        self.results_frame = ttk.Frame(self.canvas)
        self._results_window = self.canvas.create_window(
            (0, 0), window=self.results_frame, anchor="nw",
        )
        self.results_frame.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._results_window, width=e.width),
        )

        self._render_placeholder()

        # Enter in URL → run.
        self.url_entry.bind("<Return>", lambda _e: self._start_audit())

    def _clear_results(self) -> None:
        for child in self.results_frame.winfo_children():
            child.destroy()

    def _render_placeholder(self) -> None:
        self._clear_results()
        ttk.Label(
            self.results_frame,
            text="Enter a URL and click Run audit.",
            foreground="#777",
            padding=16,
        ).pack(anchor="w")

    # ------------------------------------------------------------------
    # Audit execution.

    def _start_audit(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("Missing URL", "Enter a URL first.")
            return
        if self._worker and self._worker.is_alive():
            return

        # Warn once if VLM is requested without a key — the module fails
        # closed and returns no issues, which would be confusing if the
        # user thought they'd enabled it.
        if self.vlm_var.get() and not os.environ.get("OPENROUTER_API_KEY"):
            if not messagebox.askyesno(
                "OPENROUTER_API_KEY not set",
                "VLM semantic checks are enabled but OPENROUTER_API_KEY is "
                "not set in the environment. The VLM module will skip "
                "itself and produce no findings.\n\nContinue anyway?",
            ):
                return

        options = {
            "headless": self.headless_var.get(),
            "screenshots": self.screenshots_var.get(),
            "vlm_checks": self.vlm_var.get(),
            "skip_nvda": self.skip_nvda_var.get(),
            "level": "aa",
            "timeout_seconds": 60,
            "max_tabs": 100,
        }

        self.run_btn.state(["disabled"])
        self.run_btn.configure(text="Running…")
        self.progress.start(12)
        self.status_var.set(f"Auditing {url} — typical runtime 20-60s…")
        self._clear_results()
        ttk.Label(
            self.results_frame,
            text="Audit in progress. Modules run sequentially inside a "
                 "Chromium instance — please wait.",
            foreground="#555",
            padding=16,
        ).pack(anchor="w")

        self._worker = threading.Thread(
            target=self._run_audit,
            args=(url, options),
            daemon=True,
        )
        self._worker.start()
        self.root.after(200, self._poll_queue)

    def _run_audit(self, url: str, options: dict[str, Any]) -> None:
        """Background-thread target. Never touches Tk directly — only the queue."""
        try:
            result = AuditOrchestrator(url=url, options=options).run()
            self._queue.put(("done", result))
        except Exception as exc:  # noqa: BLE001
            self._queue.put(("error", exc))

    def _poll_queue(self) -> None:
        try:
            kind, payload = self._queue.get_nowait()
        except queue.Empty:
            self.root.after(200, self._poll_queue)
            return

        self.progress.stop()
        self.run_btn.state(["!disabled"])
        self.run_btn.configure(text="Run audit")

        if kind == "error":
            self.status_var.set("Audit failed.")
            self._clear_results()
            messagebox.showerror("Audit failed", f"{type(payload).__name__}: {payload}")
            self._render_placeholder()
            return

        self._result = payload
        dur = payload.get("duration_seconds") or 0
        self.status_var.set(
            f"Done in {dur:.1f}s · {len(payload.get('issues') or [])} issues",
        )
        self._render_summary(payload)

    # ------------------------------------------------------------------
    # Summary rendering.

    def _render_summary(self, result: dict[str, Any]) -> None:
        self._clear_results()
        summary = result.get("summary") or {}

        # --- Score card.
        card = ttk.Frame(self.results_frame, padding=8)
        card.pack(fill="x")
        grade = summary.get("grade") or "?"
        score = summary.get("score")
        score_txt = str(score) if score is not None else "—"
        tk.Label(
            card,
            text=f"{grade}",
            font=("Segoe UI", 36, "bold"),
            fg=GRADE_COLORS.get(grade, "#333"),
        ).pack(side="left", padx=(0, 16))
        tk.Label(
            card,
            text=f"Score {score_txt}",
            font=("Segoe UI", 16),
        ).pack(side="left", padx=8)

        # Key stats (issues, distinct, weakest).
        stats = ttk.Frame(self.results_frame)
        stats.pack(fill="x", padx=8, pady=(0, 8))
        self._stat(stats, "Total issues", summary.get("total_issues", 0))
        distinct = summary.get("distinct_defects")
        if distinct is not None:
            self._stat(stats, "Distinct defects", distinct)
        if summary.get("weakest_principle"):
            self._stat(
                stats,
                "Weakest principle",
                summary["weakest_principle"].capitalize(),
            )

        # --- By-severity.
        sev = ttk.LabelFrame(self.results_frame, text="By severity", padding=8)
        sev.pack(fill="x", padx=8, pady=4)
        by_sev = summary.get("by_severity") or {}
        for col, s in enumerate(["critical", "serious", "moderate", "minor"]):
            tk.Label(
                sev,
                text=f"{s.capitalize()}: {by_sev.get(s, 0)}",
                fg=SEVERITY_COLORS[s],
                font=("Segoe UI", 11, "bold"),
            ).grid(row=0, column=col, padx=14, sticky="w")

        # --- By-principle bars.
        by_p = summary.get("by_principle") or {}
        if by_p:
            prin = ttk.LabelFrame(self.results_frame, text="By principle", padding=8)
            prin.pack(fill="x", padx=8, pady=4)
            for row, (name, data) in enumerate(by_p.items()):
                if not isinstance(data, dict):
                    continue
                ttk.Label(prin, text=name.capitalize(), width=16).grid(
                    row=row, column=0, sticky="w", padx=4, pady=2,
                )
                ttk.Label(prin, text=f"score {data.get('score', '—')}").grid(
                    row=row, column=1, sticky="w", padx=8,
                )
                ttk.Label(prin, text=f"{data.get('issues', 0)} issues").grid(
                    row=row, column=2, sticky="w", padx=8,
                )

        # --- Conformance badges.
        conf = summary.get("conformance") or {}
        if conf:
            cf = ttk.LabelFrame(self.results_frame, text="WCAG 2.2 conformance", padding=8)
            cf.pack(fill="x", padx=8, pady=4)
            for col, lvl in enumerate(["A", "AA", "AAA"]):
                ok = bool(conf.get(f"{lvl}_conformant"))
                tk.Label(
                    cf,
                    text=f"Level {lvl}: {'✓ conformant' if ok else '✗ fails'}",
                    fg="#1a7f37" if ok else "#b00020",
                    font=("Segoe UI", 11, "bold"),
                ).grid(row=0, column=col, padx=14, sticky="w")

        # --- Coverage warning banner.
        coverage = result.get("coverage") or {}
        # Keyboard coverage sits inside modules.keyboard.coverage in
        # some result shapes, so probe there too.
        if not coverage:
            kb = (result.get("modules") or {}).get("keyboard") or {}
            coverage = kb.get("coverage") or {}
        if coverage.get("truncated"):
            banner = tk.Label(
                self.results_frame,
                text=(
                    f"⚠ Keyboard walk truncated — "
                    f"{coverage.get('stops_walked')}/{coverage.get('max_tabs')} stops "
                    "(some issues past the cap are not included)."
                ),
                bg="#fff3e0",
                fg="#8a4a00",
                anchor="w",
                padx=8,
                pady=6,
            )
            banner.pack(fill="x", padx=8, pady=4)

        # --- Top blockers.
        issues = result.get("issues") or []
        blockers = [
            i for i in issues
            if (i.get("level") in ("A", "AA"))
            and (i.get("severity") in ("critical", "serious"))
        ][:10]
        if blockers:
            bf = ttk.LabelFrame(
                self.results_frame,
                text=f"Top blockers (A/AA, critical + serious) — showing {len(blockers)}",
                padding=6,
            )
            bf.pack(fill="x", padx=8, pady=4)
            for i, issue in enumerate(blockers):
                line = ttk.Frame(bf)
                line.pack(fill="x", pady=1)
                sev = issue.get("severity", "minor")
                tk.Label(
                    line, text=sev.upper(), fg=SEVERITY_COLORS.get(sev, "#555"),
                    width=10, anchor="w", font=("Segoe UI", 9, "bold"),
                ).pack(side="left")
                tk.Label(
                    line,
                    text=f"[{issue.get('level') or '—'}] {issue.get('title', '')}",
                    anchor="w", justify="left",
                ).pack(side="left", padx=6)
                rule = issue.get("rule", "")
                if rule:
                    tk.Label(
                        line, text=rule, fg="#777",
                        font=("Segoe UI", 9, "italic"),
                    ).pack(side="left", padx=6)

        # --- Cross-page groups (multi-URL runs).
        groups = result.get("cross_page_groups") or []
        if groups:
            gf = ttk.LabelFrame(
                self.results_frame,
                text=f"Cross-page defect groups ({len(groups)})",
                padding=6,
            )
            gf.pack(fill="x", padx=8, pady=4)
            for g in groups[:10]:
                txt = (
                    f"[{g.get('severity', '—')}] "
                    f"{g.get('title') or g.get('rule')} — "
                    f"{g.get('instance_count', 0)} instances across "
                    f"{len(g.get('pages_affected') or [])} pages"
                )
                tk.Label(gf, text=txt, anchor="w").pack(fill="x", pady=1)

        # --- Modules run status.
        modf = ttk.LabelFrame(self.results_frame, text="Modules", padding=6)
        modf.pack(fill="x", padx=8, pady=4)
        modules = result.get("modules") or {}
        for row, (name, data) in enumerate(modules.items()):
            if not isinstance(data, dict):
                continue
            if data.get("ran"):
                status = "ran"
                color = "#1a7f37"
            elif data.get("error"):
                status = f"error: {data['error'][:80]}"
                color = "#b00020"
            else:
                status = "skipped"
                color = "#999"
            ttk.Label(modf, text=name, width=18).grid(row=row, column=0, sticky="w", padx=4, pady=1)
            tk.Label(modf, text=status, fg=color, anchor="w").grid(
                row=row, column=1, sticky="w", padx=4,
            )
            ttk.Label(modf, text=f"{data.get('issues_found', 0)} issues").grid(
                row=row, column=2, sticky="w", padx=8,
            )
            ttk.Label(
                modf,
                text=f"{float(data.get('duration_seconds', 0) or 0):.2f}s",
                foreground="#777",
            ).grid(row=row, column=3, sticky="w", padx=4)

        # --- Action buttons.
        actions = ttk.Frame(self.results_frame)
        actions.pack(fill="x", padx=8, pady=(14, 8))
        ttk.Button(
            actions, text="Open full HTML report", command=self._open_report,
        ).pack(side="left", padx=4)
        ttk.Button(
            actions, text="Save JSON…", command=self._save_json,
        ).pack(side="left", padx=4)

    @staticmethod
    def _stat(parent: tk.Widget, label: str, value: Any) -> None:
        wrap = ttk.Frame(parent)
        wrap.pack(side="left", padx=12)
        ttk.Label(wrap, text=label, foreground="#777").pack(anchor="w")
        tk.Label(wrap, text=str(value), font=("Segoe UI", 14, "bold")).pack(anchor="w")

    # ------------------------------------------------------------------
    # Export actions.

    def _open_report(self) -> None:
        if not self._result:
            return
        try:
            html = self._render_html(self._result)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Report render failed", f"{type(exc).__name__}: {exc}")
            return
        # NamedTemporaryFile gives us a unique, atomically-created path
        # in the user's temp dir; PID-based naming used to collide when
        # the GUI was relaunched (same PID after a daemonizing exec) or
        # run alongside another instance from the same shell.
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".html",
            prefix="autoaudit_",
            delete=False,
            encoding="utf-8",
        ) as fh:
            fh.write(html)
            target = Path(fh.name)
        webbrowser.open(target.as_uri())

    def _save_json(self) -> None:
        if not self._result:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            initialfile="audit.json",
        )
        if not path:
            return
        Path(path).write_text(
            json.dumps(self._result, indent=2, default=str),
            encoding="utf-8",
        )
        messagebox.showinfo("Saved", f"Wrote {path}")

    def _render_html(self, result: dict[str, Any]) -> str:
        from jinja2 import Environment, FileSystemLoader

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=True,
        )
        tmpl = env.get_template("report.html.j2")
        # The template references `audit.job_id` for the raw-JSON link;
        # populate a placeholder so rendering never trips.
        data = dict(result)
        data.setdefault("job_id", f"local-{os.getpid()}")
        data.setdefault("status", "completed")
        return tmpl.render(audit=data)


def main() -> None:
    root = tk.Tk()
    # Try for a slightly more modern look on Windows.
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    AuditApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
