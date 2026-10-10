"""Pictures: placeholders always work, and a designer's SVG takes over just by existing."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from contextbridge.gui import assets, theme

RED_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
    '<rect width="100" height="100" fill="#FF0000"/></svg>'
)


@pytest.fixture(scope="module")
def qapp() -> Iterator[QApplication]:
    yield QApplication.instance() or QApplication(["pytest"])  # type: ignore[misc]


@pytest.fixture
def assets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(assets, "ASSETS_DIR", tmp_path)
    return tmp_path


def _has_content(image: QImage) -> bool:
    return any(
        image.pixelColor(x, y).alpha() > 0
        for x in range(0, image.width(), 6)
        for y in range(0, image.height(), 6)
    )


@pytest.mark.parametrize("name", assets.ILLUSTRATIONS)
def test_placeholder_illustrations_render(qapp: QApplication, assets_dir: Path, name: str) -> None:
    pm = assets.illustration(name, 120)
    assert not pm.isNull() and pm.width() == 240  # drawn at 2x for sharp screens
    assert _has_content(pm.toImage())


@pytest.mark.parametrize("name", assets.NAV_ICONS)
def test_placeholder_nav_icons_render(qapp: QApplication, assets_dir: Path, name: str) -> None:
    icon = assets.nav_icon(name)
    assert not icon.isNull() and _has_content(icon.pixmap(22, 22).toImage())


def test_unknown_names_fall_back_instead_of_crashing(qapp: QApplication, assets_dir: Path) -> None:
    assert not assets.illustration("no-such-picture", 80).isNull()
    assert not assets.nav_icon("no-such-icon").isNull()


def test_a_designers_svg_replaces_the_placeholder(qapp: QApplication, assets_dir: Path) -> None:
    (assets_dir / "welcome.svg").write_text(RED_SVG)
    image = assets.illustration("welcome", 100, dpr=1.0).toImage()
    centre = image.pixelColor(50, 50)
    assert (centre.red(), centre.green(), centre.blue()) == (255, 0, 0)
    # other pictures are untouched
    assert assets.illustration("notes", 100, dpr=1.0).toImage().pixelColor(50, 50) != QColor(
        "#FF0000"
    )


def test_a_designers_icons_and_app_icon_are_used(qapp: QApplication, assets_dir: Path) -> None:
    (assets_dir / "icon_home.svg").write_text(RED_SVG)
    (assets_dir / "app_icon.svg").write_text(RED_SVG)
    nav = assets.nav_icon("home", size=22).pixmap(22, 22).toImage()
    assert nav.pixelColor(11, 11).red() == 255 and nav.pixelColor(11, 11).green() == 0
    app = assets.app_icon().pixmap(64, 64).toImage()
    assert app.pixelColor(32, 32).red() == 255 and app.pixelColor(32, 32).green() == 0


def test_a_broken_svg_falls_back_to_the_placeholder(qapp: QApplication, assets_dir: Path) -> None:
    (assets_dir / "done.svg").write_text("this is not an svg")
    pm = assets.illustration("done", 100)
    assert not pm.isNull() and _has_content(pm.toImage())


def test_app_icon_placeholder_has_the_palette(qapp: QApplication, assets_dir: Path) -> None:
    image = assets.app_icon_pixmap(256).toImage()
    corner, middle = image.pixelColor(4, 4), image.pixelColor(30, 128)
    assert corner.alpha() == 0  # a rounded square, not a full-bleed block
    assert middle.name().upper() == theme.RED_CHAI


def test_indicator_images_are_written(qapp: QApplication) -> None:
    paths = assets.indicator_images()
    assert set(paths) == {"check", "radio", "chevron"}
    for path in paths.values():
        assert Path(path).is_file() and Path(path.replace(".png", "@2x.png")).is_file()


def test_bundled_assets_folder_is_documented() -> None:
    from contextbridge.gui.assets import ASSETS_DIR

    assert ASSETS_DIR.name == "assets"
    assert (ASSETS_DIR / "README.md").is_file()
