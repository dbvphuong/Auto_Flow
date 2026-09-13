import qdarktheme


THEME_ORDER = ("dark", "light", "lavender")
THEME_NAMES = {"dark": "Tối", "light": "Sáng", "lavender": "Tím nhạt"}

THEME_PALETTES = {
    "dark": {
        "window": "#181825", "sidebar": "#1e1e2e", "surface": "#252536",
        "surface_hover": "#313244", "text": "#f4f4f5", "muted": "#bac2de",
        "border": "#45475a", "accent": "#8b5cf6", "accent_text": "#ffffff",
    },
    "light": {
        "window": "#f5f7fa", "sidebar": "#ffffff", "surface": "#ffffff",
        "surface_hover": "#e8edf3", "text": "#1f2937", "muted": "#526071",
        "border": "#cbd5e1", "accent": "#2563eb", "accent_text": "#ffffff",
    },
    "lavender": {
        "window": "#f6f1ff", "sidebar": "#eee5ff", "surface": "#ffffff",
        "surface_hover": "#dfd2f4", "text": "#30243f", "muted": "#665777",
        "border": "#c7b6dc", "accent": "#7c3aed", "accent_text": "#ffffff",
    },
}


def normalize_theme(theme):
    return theme if theme in THEME_ORDER else "dark"


def next_theme(theme):
    current = normalize_theme(theme)
    return THEME_ORDER[(THEME_ORDER.index(current) + 1) % len(THEME_ORDER)]


def theme_palette(theme):
    return THEME_PALETTES[normalize_theme(theme)]


def theme_stylesheet(theme):
    theme = normalize_theme(theme)
    colors = theme_palette(theme)
    base_theme = "dark" if theme == "dark" else "light"
    return qdarktheme.load_stylesheet(base_theme) + f"""
        QMainWindow, QDialog {{
            background-color: {colors['window']};
            color: {colors['text']};
        }}
        QToolTip {{
            background-color: {colors['surface']};
            color: {colors['text']};
            border: 1px solid {colors['border']};
        }}
        QGroupBox {{ color: {colors['text']}; border-color: {colors['border']}; }}
        QLabel, QCheckBox, QRadioButton {{ color: {colors['text']}; }}
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox,
        QDoubleSpinBox, QListWidget, QTableWidget {{
            background-color: {colors['surface']};
            color: {colors['text']};
            border-color: {colors['border']};
            selection-background-color: {colors['accent']};
            selection-color: {colors['accent_text']};
        }}
        QHeaderView::section {{
            background-color: {colors['surface_hover']};
            color: {colors['text']};
            border-color: {colors['border']};
        }}
        QMenu {{
            background-color: {colors['surface']};
            color: {colors['text']};
            border: 1px solid {colors['border']};
        }}
        QMenu::item:selected {{
            background-color: {colors['accent']};
            color: {colors['accent_text']};
        }}
    """
