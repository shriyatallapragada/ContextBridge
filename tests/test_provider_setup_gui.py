"""First-run provider setup and project settings with no network requests."""

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from contextbridge.config import load_config
from contextbridge.gui.app import configure_app
from contextbridge.gui.mainwindow import MainWindow
from contextbridge.providers.http import HTTPProvider
from tests.gui_helpers import settle


def test_first_run_defaults_to_offline_and_can_finish_without_models(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    configure_app(app)
    monkeypatch.setattr(
        HTTPProvider, "request", lambda *_: (_ for _ in ()).throw(AssertionError("network request"))
    )
    window = MainWindow(
        QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat), restore_last=False
    )
    window.show()
    settle(app, window)
    assert window.root.currentWidget() is window.setup_page
    assert window.setup_page.form.off_radio.isChecked()
    window.setup_page.form.save_button.click()
    settle(app, window)
    assert window.root.currentWidget() is window.welcome
    assert load_config().llm.setup_complete and load_config().llm.provider == "none"
    window.close()


def test_first_run_offers_local_and_api_options(tmp_path):
    app = QApplication.instance() or QApplication([])
    configure_app(app)
    window = MainWindow(
        QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat), restore_last=False
    )
    window.show()
    settle(app, window)
    form = window.setup_page.form
    form.api_radio.setChecked(True)
    assert form.key.isVisibleTo(form)
    assert form.privacy.isVisibleTo(form)
    form.local_radio.setChecked(True)
    assert not form.key.isVisibleTo(form)
    assert form.endpoint.text().startswith("http://")
    window.close()


def test_typed_model_survives_refresh_check_and_save_then_reaches_cli(git_repo, monkeypatch):
    import json

    from typer.testing import CliRunner

    from contextbridge.gui.provider_form import ProviderForm
    from contextbridge.gui.workers import TaskRunner
    from contextbridge.main import app as cli_app
    from contextbridge.providers import credentials
    from contextbridge.providers.settings import ProviderSettingsService
    from contextbridge.service import ProjectService
    from tests.gui_helpers import wait_for

    app = QApplication.instance() or QApplication([])
    configure_app(app)
    project = ProjectService(git_repo)
    project.init(install_hook=False)
    monkeypatch.setattr(credentials, "get_key", lambda endpoint: "saved-test-key")
    service = ProviderSettingsService(project.ws.context_dir)
    endpoint = "https://api.groq.com/openai/v1"
    service.save("openai-compatible", "llama-3.1-8b-instant", endpoint)
    calls = []

    def request(self, path, body=None):
        calls.append((self.model, self.endpoint, path, body))
        prompt = body["messages"][0]["content"]
        content = (
            "OK"
            if prompt == "Reply with OK."
            else json.dumps({"lines": json.loads(prompt)["lines"]})
        )
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(HTTPProvider, "request", request)
    saved = []
    form = ProviderForm(TaskRunner(), service, lambda: saved.append(True))
    form.show()
    model = "llama-3.3-70b-versatile"
    form.model.setText(model)
    # MainWindow refreshes every page when new project snapshots arrive.
    form.service = ProviderSettingsService(project.ws.context_dir)
    form.reload()
    assert form.model.text() == model
    form.check_button.click()
    wait_for(app, lambda: form.check_button.isEnabled())
    assert "responded successfully" in form.message.text()
    assert model in form.message.text()
    assert service.load().llm.model == "llama-3.1-8b-instant"  # Check does not save.
    form.save_button.click()
    wait_for(app, lambda: bool(saved))
    form.reload()
    assert form.model.text() == service.load().llm.model == model
    result = CliRunner().invoke(cli_app, ["handoff", "--to", "codex", "--dry-run"])
    assert result.exit_code == 0 and "openai-compatible" in result.output
    assert len(calls) == 2 and all(call[0] == model and call[1] == endpoint for call in calls)
    assert calls[0][3]["max_tokens"] == 8
    form.close()
