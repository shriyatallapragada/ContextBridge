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
