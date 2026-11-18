"""Provider selection UI backed by the engine's configuration service."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QButtonGroup, QLineEdit, QRadioButton, QVBoxLayout, QWidget

from contextbridge.gui.widgets import button, label
from contextbridge.gui.workers import TaskRunner
from contextbridge.providers.settings import ProviderSettingsService, ProviderStatus


class ProviderForm(QWidget):
    def __init__(
        self,
        runner: TaskRunner,
        service: ProviderSettingsService,
        on_saved: Callable[[], None],
        *,
        first_run: bool = False,
    ) -> None:
        super().__init__()
        self.runner, self.service, self.on_saved = runner, service, on_saved
        layout = QVBoxLayout(self)
        layout.addWidget(
            label(
                "ContextBridge works fully offline without a model or API key. "
                "Models are optional for clearer explanations."
            )
        )
        self.off_radio = QRadioButton("Offline — no model")
        self.local_radio = QRadioButton("Ollama on this computer")
        self.api_radio = QRadioButton("API endpoint — uses your API key")
        self.group = QButtonGroup(self)
        for radio in (self.off_radio, self.local_radio, self.api_radio):
            self.group.addButton(radio)
            layout.addWidget(radio)
            radio.toggled.connect(self._selection_changed)
        self.fields = QWidget()
        fields = QVBoxLayout(self.fields)
        self.model = QLineEdit()
        self.model.setAccessibleName("Provider model")
        self.endpoint = QLineEdit()
        self.endpoint.setAccessibleName("Provider endpoint")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setAccessibleName("Provider API key")
        self.key.setPlaceholderText("API key — stored in your OS keyring")
        fields.addWidget(label("Model name"))
        fields.addWidget(self.model)
        fields.addWidget(label("Endpoint URL"))
        fields.addWidget(self.endpoint)
        fields.addWidget(self.key)
        self.privacy = label(
            "Selected handoff text is sent to the API endpoint only when you use the model. "
            "The API key is saved in your operating system keyring."
        )
        fields.addWidget(self.privacy)
        self.check_button = button("Check connection")
        self.check_button.clicked.connect(self._check)
        fields.addWidget(self.check_button)
        layout.addWidget(self.fields)
        self.message = label("", "Muted")
        layout.addWidget(self.message)
        self.save_button = button("Continue" if first_run else "Save model settings", "Primary")
        self.save_button.clicked.connect(self._save)
        layout.addWidget(self.save_button)
        self._loaded = False
        self.reload()

    def reload(self) -> None:
        config = self.service.load().llm
        self._loaded = False
        self.off_radio.setChecked(config.provider == "none")
        self.local_radio.setChecked(config.provider == "ollama")
        self.api_radio.setChecked(config.provider not in ("none", "ollama"))
        self.model.setText(
            config.model
            if config.provider in ("none", "ollama", "openai-compatible")
            else config.cloud_model_for(config.provider)
        )
        from contextbridge.providers import DEFAULT_ENDPOINTS

        self.endpoint.setText(
            config.ollama_host
            if config.provider in ("none", "ollama")
            else DEFAULT_ENDPOINTS.get(config.provider, config.base_url)
        )
        self._loaded = True
        self._selection_changed()

    def provider(self) -> str:
        return (
            "none"
            if self.off_radio.isChecked()
            else "ollama"
            if self.local_radio.isChecked()
            else "openai-compatible"
        )

    def _selection_changed(self) -> None:
        mode = self.provider()
        self.fields.setVisible(mode != "none")
        self.key.setVisible(mode == "openai-compatible")
        self.privacy.setVisible(mode == "openai-compatible")
        if self._loaded:
            config = self.service.load().llm
            if mode == "ollama":
                self.endpoint.setText(config.ollama_host)
                self.model.setText(
                    config.model if config.provider in ("none", "ollama") else "qwen2.5-coder:7b"
                )
            elif mode == "openai-compatible" and config.provider in ("none", "ollama"):
                self.endpoint.setText(config.base_url)
                self.model.clear()
        self.message.clear()

    def _save(self) -> None:
        values = (self.provider(), self.model.text(), self.endpoint.text(), self.key.text())
        self.save_button.setEnabled(False)

        def work(_emit: object) -> None:
            self.service.save(*values)

        def done(_result: object) -> None:
            self.save_button.setEnabled(True)
            self.key.clear()
            self.message.setText("Model settings saved.")
            self.on_saved()

        def failed(exc: Exception) -> None:
            self.save_button.setEnabled(True)
            self.message.setText(str(exc))

        self.runner.run(work, on_done=done, on_error=failed)

    def _check(self) -> None:
        values = (self.provider(), self.model.text(), self.endpoint.text(), self.key.text())
        self.check_button.setEnabled(False)

        def done(result: ProviderStatus) -> None:
            self.check_button.setEnabled(True)
            self.message.setText(result.message)

        def failed(_exc: Exception) -> None:
            self.check_button.setEnabled(True)
            self.message.setText(
                "The connection could not be checked. Offline mode remains available."
            )

        self.runner.run(lambda _emit: self.service.check(*values), on_done=done, on_error=failed)
