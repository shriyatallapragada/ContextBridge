"""First-run setup, with offline operation preselected."""

from collections.abc import Callable

from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from contextbridge.gui.pages.base import Host
from contextbridge.gui.provider_form import ProviderForm
from contextbridge.gui.widgets import label
from contextbridge.providers.settings import ProviderSettingsService


class SetupPage(QScrollArea):
    def __init__(self, host: Host, on_saved: Callable[[], None]) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.addWidget(label("Welcome to ContextBridge", "H1"))
        self.form = ProviderForm(host.runner, ProviderSettingsService(), on_saved, first_run=True)
        layout.addWidget(self.form)
        layout.addStretch(1)
        self.setWidget(content)
