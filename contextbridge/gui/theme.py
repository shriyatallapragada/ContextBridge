"""Colours, spacing and the app stylesheet. The whole look lives in this file.

The four base colours come from the "Red Chai / Masala / Cinnamon Toast / Cardamom"
swatches. Everything else is a tint or shade of them, so the app stays one calm family.
No Qt imports here: the contrast rules in :data:`TEXT_PAIRS` are unit-tested directly.
"""

from __future__ import annotations

# ----- the swatches -----------------------------------------------------------------------

RED_CHAI = "#632626"
MASALA = "#9D5353"
CINNAMON_TOAST = "#BF8B67"
CARDAMOM = "#DBCB96"
WHITE = "#FFFFFF"


def _rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def mix(a: str, b: str, t: float) -> str:
    """Blend ``a`` towards ``b``: t=0 is ``a``, t=1 is ``b``."""
    ra, rb = _rgb(a), _rgb(b)
    out = tuple(round(x + (y - x) * t) for x, y in zip(ra, rb, strict=True))
    return "#{:02X}{:02X}{:02X}".format(*out)


def luminance(color: str) -> float:
    def channel(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two colours (1 to 21)."""
    hi, lo = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# ----- roles -------------------------------------------------------------------------------

BG = mix(CARDAMOM, WHITE, 0.80)  # page: a whisper of Cardamom
SURFACE = mix(CARDAMOM, WHITE, 0.92)  # cards sit slightly lighter than the page
SURFACE_ALT = mix(CARDAMOM, WHITE, 0.62)  # tags, quiet fills
BORDER = mix(CARDAMOM, WHITE, 0.45)
TEXT = RED_CHAI
TEXT_MUTED = mix(RED_CHAI, WHITE, 0.22)
ON_DARK = mix(CARDAMOM, WHITE, 0.88)  # cream text on the dark colours
ON_DARK_MUTED = CARDAMOM
SIDEBAR = RED_CHAI
PRIMARY = MASALA
PRIMARY_HOVER = mix(MASALA, RED_CHAI, 0.45)
PRIMARY_PRESSED = RED_CHAI
ACCENT = CINNAMON_TOAST  # decorative only: lines, icons, focus rings (not text)
FOCUS = CINNAMON_TOAST
OUTPUT_BG = mix(RED_CHAI, "#000000", 0.30)  # the test-output console

# (foreground, background, description): every text pair used must be >= 4.5:1 (WCAG AA).
TEXT_PAIRS: list[tuple[str, str, str]] = [
    (TEXT, BG, "body text on page"),
    (TEXT, SURFACE, "body text on cards"),
    (TEXT, SURFACE_ALT, "text on tags"),
    (TEXT_MUTED, BG, "muted text on page"),
    (TEXT_MUTED, SURFACE, "muted text on cards"),
    (ON_DARK, PRIMARY, "button label"),
    (ON_DARK, PRIMARY_HOVER, "button label (hover)"),
    (ON_DARK, PRIMARY_PRESSED, "button label (pressed)"),
    (ON_DARK, SIDEBAR, "sidebar text"),
    (ON_DARK_MUTED, SIDEBAR, "sidebar secondary text"),
    (ON_DARK, PRIMARY, "selected sidebar item"),
    (TEXT, CARDAMOM, "text on highlight"),
    (ON_DARK, OUTPUT_BG, "test output console"),
]

# ----- spacing and shape ---------------------------------------------------------------------

SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = 12
RADIUS_SMALL = 8
BASE_FONT_PT = 14
SIDEBAR_WIDTH = 208


def build_stylesheet(indicators: dict[str, str] | None = None) -> str:
    """The Qt stylesheet for the whole app.

    ``indicators`` maps "check" and "radio" to image paths for tick marks (made at startup).
    """
    ind = indicators or {}
    check_img = f"image: url({ind['check']});" if "check" in ind else ""
    radio_img = f"image: url({ind['radio']});" if "radio" in ind else ""
    chevron_img = f"image: url({ind['chevron']});" if "chevron" in ind else ""
    return f"""
* {{ outline: none; }}
QWidget {{ color: {TEXT}; font-size: {BASE_FONT_PT}pt; }}
QMainWindow, QWidget#Page, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{
    background: {BG};
}}
QScrollArea {{ border: none; }}

/* ---- sidebar ---- */
QFrame#Sidebar {{ background: {SIDEBAR}; }}
QFrame#Sidebar QLabel {{ background: transparent; color: {ON_DARK}; }}
QLabel#Brand {{ font-size: 17pt; font-weight: 600; color: {ON_DARK}; }}
QLabel#BrandSub {{ font-size: 11pt; color: {ON_DARK_MUTED}; }}
QPushButton#NavButton {{
    background: transparent; color: {ON_DARK}; border: none; text-align: left;
    padding: 10px 14px; border-radius: {RADIUS_SMALL}px; font-size: 13pt;
}}
QPushButton#NavButton:hover {{ background: {mix(SIDEBAR, MASALA, 0.45)}; }}
QPushButton#NavButton:checked {{ background: {PRIMARY}; color: {ON_DARK}; font-weight: 600; }}
QPushButton#NavButton:focus {{ border: 2px solid {FOCUS}; }}
QLabel#ProjectName {{ color: {ON_DARK}; font-size: 12pt; font-weight: 600; }}
QLabel#ProjectPath {{ color: {ON_DARK_MUTED}; font-size: 10pt; }}

/* ---- type ---- */
QLabel#H1 {{ font-size: 24pt; font-weight: 600; }}
QLabel#H2 {{ font-size: 16pt; font-weight: 600; }}
QLabel#Eyebrow {{ font-size: 10pt; font-weight: 700; color: {TEXT_MUTED}; }}
QLabel#Muted {{ color: {TEXT_MUTED}; }}
QLabel#Small {{ color: {TEXT_MUTED}; font-size: 11pt; }}

/* ---- cards ---- */
QFrame#Card {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
}}
QFrame#Card QLabel {{ background: transparent; }}
QFrame#CardSelected {{
    background: {SURFACE}; border: 2px solid {PRIMARY}; border-radius: {RADIUS}px;
}}
QFrame#CardSelected QLabel {{ background: transparent; }}
QFrame#Notice {{
    background: {mix(CARDAMOM, WHITE, 0.45)}; border: 1px solid {CARDAMOM};
    border-radius: {RADIUS}px;
}}
QFrame#Notice QLabel {{ background: transparent; }}
QLabel#Tag, QFrame#Card QLabel#Tag {{
    background: {SURFACE_ALT}; color: {TEXT}; border-radius: 9px; padding: 2px 10px;
    font-size: 11pt; font-weight: 600;
}}

/* ---- buttons ---- */
QPushButton {{
    background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: {RADIUS_SMALL}px; padding: 9px 18px;
}}
QPushButton:hover {{ border-color: {ACCENT}; background: {mix(CARDAMOM, WHITE, 0.7)}; }}
QPushButton:pressed {{ background: {SURFACE_ALT}; }}
QPushButton:focus {{ border: 2px solid {FOCUS}; }}
QPushButton:disabled {{ color: {mix(TEXT, BG, 0.55)}; border-color: {BORDER}; background: {BG}; }}
QPushButton#Primary {{
    background: {PRIMARY}; color: {ON_DARK}; border: 1px solid {PRIMARY}; font-weight: 600;
    padding: 11px 24px;
}}
QPushButton#Primary:hover {{ background: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; }}
QPushButton#Primary:pressed {{ background: {PRIMARY_PRESSED}; }}
QPushButton#Primary:focus {{ border: 2px solid {FOCUS}; }}
QPushButton#Primary:disabled {{ background: {mix(PRIMARY, BG, 0.6)}; color: {BG}; border-color: transparent; }}
QPushButton#Danger {{ color: {PRIMARY_PRESSED}; border: 1px solid {MASALA}; }}
QPushButton#Danger:hover {{ background: {mix(MASALA, WHITE, 0.85)}; }}
QPushButton#Link {{
    border: none; background: transparent; color: {PRIMARY}; text-decoration: underline;
    padding: 4px 2px; text-align: left;
}}
QPushButton#Link:hover {{ color: {PRIMARY_PRESSED}; background: transparent; }}
QPushButton#Chip {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 16px; padding: 6px 14px;
    font-size: 12pt;
}}
QPushButton#Chip:checked {{ background: {CARDAMOM}; border: 2px solid {PRIMARY}; font-weight: 600; }}

QPushButton#ChoiceCard {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS}px; text-align: left;
}}
QPushButton#ChoiceCard:hover {{ border-color: {ACCENT}; background: {mix(CARDAMOM, WHITE, 0.78)}; }}
QPushButton#ChoiceCard:checked {{ border: 2px solid {PRIMARY}; background: {mix(CARDAMOM, WHITE, 0.55)}; }}
QPushButton#ChoiceCard:focus {{ border: 2px solid {FOCUS}; }}
QPushButton#ChoiceCard QLabel {{ background: transparent; }}

/* ---- inputs ---- */
QPlainTextEdit, QLineEdit, QTextEdit {{
    background: {WHITE}; border: 1px solid {BORDER}; border-radius: {RADIUS_SMALL}px;
    padding: 10px 12px; selection-background-color: {CARDAMOM}; selection-color: {TEXT};
}}
QPlainTextEdit:focus, QLineEdit:focus, QTextEdit:focus {{ border: 2px solid {FOCUS}; }}
QPlainTextEdit#Output {{
    background: {OUTPUT_BG}; color: {ON_DARK}; border: none;
    font-family: Menlo, Consolas, "DejaVu Sans Mono", monospace; font-size: 12pt;
}}
QComboBox {{
    background: {WHITE}; border: 1px solid {BORDER}; border-radius: {RADIUS_SMALL}px;
    padding: 8px 12px;
}}
QComboBox:focus {{ border: 2px solid {FOCUS}; }}
QComboBox::drop-down {{ border: none; width: 30px; }}
QComboBox::down-arrow {{ {chevron_img} width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{
    background: {WHITE}; selection-background-color: {CARDAMOM}; selection-color: {TEXT};
    border: 1px solid {BORDER};
}}
QCheckBox, QRadioButton {{ spacing: 10px; background: transparent; }}
QCheckBox::indicator {{
    width: 18px; height: 18px; border: 2px solid {MASALA}; border-radius: 6px; background: {WHITE};
}}
QCheckBox::indicator:checked {{ background: {PRIMARY}; border-color: {PRIMARY}; {check_img} }}
QCheckBox::indicator:disabled {{ border-color: {BORDER}; background: {BG}; }}
QRadioButton::indicator {{
    width: 18px; height: 18px; border: 2px solid {MASALA}; border-radius: 11px; background: {WHITE};
}}
QRadioButton::indicator:checked {{ border-color: {PRIMARY}; {radio_img} }}
QCheckBox:focus, QRadioButton:focus {{ color: {TEXT}; }}

/* ---- misc ---- */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QProgressBar {{ background: {SURFACE_ALT}; border: none; border-radius: 4px; height: 8px;
    text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {PRIMARY}; border-radius: 4px; }}
QToolTip {{ background: {RED_CHAI}; color: {ON_DARK}; border: none; padding: 6px 10px; }}
QMessageBox, QDialog {{ background: {BG}; }}
"""
