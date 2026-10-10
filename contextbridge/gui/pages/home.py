"""Home: where things stand, at a glance, and the way to start a handoff."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QVBoxLayout, QWidget

from contextbridge.core.git_observer import HookAction
from contextbridge.gui import wording
from contextbridge.gui.pages.base import Host, ProjectPage
from contextbridge.gui.widgets import Card, Notice, button, clear_layout, label
from contextbridge.service import Snapshot


class HomePage(ProjectPage):
    def __init__(self, host: Host, parent: QWidget | None = None) -> None:
        super().__init__(host, parent)
        self.heading_box = QVBoxLayout()
        self.heading_box.setSpacing(6)
        self.body.addLayout(self.heading_box)
        self.dynamic = QVBoxLayout()
        self.dynamic.setSpacing(18)
        self.body.addLayout(self.dynamic)
        self.body.addStretch(1)
        self.hook_checkbox: QCheckBox | None = None

    # ----- first-time setup ---------------------------------------------------------------

    def _setup_card(self) -> Card:
        svc = self.host.service
        assert svc is not None
        card = Card()
        card.body.addWidget(label("SET UP", "Eyebrow"))
        card.body.addWidget(label("Let's get this project ready", "H2"))
        card.body.addWidget(
            label(
                "ContextBridge will keep a private notebook for this project in a hidden "
                "folder called .context. It never changes your code, and nothing leaves "
                "your computer.",
                "Muted",
            )
        )
        is_repo = svc.is_git_repo()
        self.hook_checkbox = QCheckBox("Remember my commits automatically")
        self.hook_checkbox.setChecked(is_repo)
        self.hook_checkbox.setEnabled(is_repo)
        self.hook_checkbox.setToolTip(
            "Adds a small helper to Git so each commit is noted down. You can turn it off any time."
            if is_repo
            else "This folder isn't tracked by Git yet, so there are no commits to remember."
        )
        card.body.addSpacing(6)
        card.body.addWidget(self.hook_checkbox)
        if not is_repo:
            card.body.addWidget(
                label(
                    "This folder isn't tracked by Git, so ContextBridge will only keep your notes.",
                    "Small",
                )
            )
        go = button("Set up this project", "Primary")
        go.clicked.connect(self._do_setup)
        row = QHBoxLayout()
        row.addWidget(go)
        row.addStretch(1)
        card.body.addSpacing(8)
        card.body.addLayout(row)
        return card

    def _do_setup(self) -> None:
        svc = self.host.service
        if svc is None:
            return
        want_hook = bool(self.hook_checkbox and self.hook_checkbox.isChecked())

        def work(_emit: object) -> object:
            return svc.init(install_hook=want_hook)

        def done(result: object) -> None:
            self.host.toast("All set. You're ready to go.")
            hook = getattr(result, "hook", None)
            if hook is not None and hook.action in (
                HookAction.disabled_hook,
                HookAction.unsupported_shell,
            ):
                self.host.toast(
                    "Set up, but your existing Git hook was left alone, so commits won't be noted."
                )
            self.host.refresh()

        self.host.runner.run(work, on_done=done, on_error=self.host.report_error)

    # ----- the dashboard -----------------------------------------------------------------------

    def refresh(self, snap: Snapshot | None) -> None:
        clear_layout(self.heading_box)
        clear_layout(self.dynamic)
        svc = self.service
        if svc is None:
            return
        self.heading_box.addWidget(label("HOME", "Eyebrow"))
        self.heading_box.addWidget(label(svc.name, "H1"))
        if not svc.is_initialized():
            self.heading_box.addWidget(label("Welcome! One quick step and you're ready.", "Muted"))
            self.dynamic.addWidget(self._setup_card())
            return
        self.heading_box.addWidget(label("Here's where things stand.", "Muted"))
        if snap is None:
            self.dynamic.addWidget(label("Looking at your project…", "Muted"))
            return
        ctx = snap.context

        stats = QHBoxLayout()
        stats.setSpacing(14)
        notes = len(ctx.decisions)
        stats.addWidget(
            self._stat("NOTES", str(notes), "saved so far" if notes else "none yet", "notes")
        )
        changed = len(ctx.files) + ctx.omitted_files
        unsaved = sum(f.uncommitted for f in ctx.files)
        stats.addWidget(
            self._stat(
                "CHANGES",
                str(changed),
                f"{unsaved} not saved to Git yet"
                if unsaved
                else ("all saved to Git" if changed else "no changes yet"),
                "changes",
            )
        )
        sentence, _ok = wording.test_summary(ctx.test_run)
        stats.addWidget(
            self._stat(
                "TESTS",
                sentence,
                "" if ctx.test_run is None else wording.when(ctx.test_run.timestamp),
                "changes",
                small=True,
            )
        )
        self.dynamic.addLayout(stats)

        self.dynamic.addWidget(self._right_now(snap))
        self.dynamic.addWidget(self._last_handoff(snap))

        actions = QHBoxLayout()
        go = button("Hand off to another assistant", "Primary")
        go.clicked.connect(lambda: self.host.go("handoff"))
        add = button("Add a note")
        add.clicked.connect(lambda: self.host.go("notes"))
        actions.addWidget(go)
        actions.addWidget(add)
        actions.addStretch(1)
        self.dynamic.addLayout(actions)

    def _stat(
        self, eyebrow: str, headline: str, sub: str, target: str, *, small: bool = False
    ) -> Card:
        card = Card()
        card.body.addWidget(label(eyebrow, "Eyebrow"))
        card.body.addWidget(label(headline, "H2" if small else "H1"))
        if sub:
            card.body.addWidget(label(sub, "Small"))
        card.body.addStretch(1)
        open_btn = button("Open", "Link")
        open_btn.clicked.connect(lambda: self.host.go(target))
        card.body.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _right_now(self, snap: Snapshot) -> Card:
        ctx = snap.context
        card = Card()
        card.body.addWidget(label("RIGHT NOW", "Eyebrow"))
        items: list[str] = []
        items += [f"Stuck on: {d.content}" for d in ctx.decisions_tagged("blocker")[:3]]
        run = ctx.test_run
        if ctx.tests_failing and run is not None:
            names = [str(f.get("test", "")) for f in run.failures[:3] if f.get("test")]
            items += [f"Failing test: {n}" for n in names] or ["The last test run didn't pass"]
        items += [f"To do: {d.content}" for d in ctx.decisions_tagged("todo")[:3]]
        if items:
            for text in items[:6]:
                card.body.addWidget(label(f"•  {text}"))
        else:
            card.body.addWidget(
                label(
                    "Nothing urgent. Add a note whenever something is worth remembering.", "Muted"
                )
            )
        return card

    def _last_handoff(self, snap: Snapshot) -> Notice:
        last = snap.last_handoff
        if not last:
            return Notice(
                "You haven't handed off yet. When you're ready to switch assistants, use the button below."
            )
        src = wording.AGENT_LABELS.get(str(last.get("from")), str(last.get("from", "")).title())
        dst = wording.AGENT_LABELS.get(str(last.get("to")), str(last.get("to", "")).title())
        return Notice(f"Last handoff: {src} → {dst}, {wording.when(str(last.get('timestamp')))}.")
