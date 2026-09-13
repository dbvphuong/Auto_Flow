from ui.theme import THEME_ORDER, next_theme, normalize_theme, theme_palette, theme_stylesheet


def test_theme_cycle_wraps_through_all_options():
    assert next_theme("dark") == "light"
    assert next_theme("light") == "lavender"
    assert next_theme("lavender") == "dark"


def test_invalid_theme_falls_back_to_dark():
    assert normalize_theme("unknown") == "dark"
    assert normalize_theme(None) == "dark"


def test_every_theme_has_distinct_text_and_background_colors():
    assert set(THEME_ORDER) == {"dark", "light", "lavender"}
    for theme in THEME_ORDER:
        colors = theme_palette(theme)
        assert colors["text"] != colors["window"]
        stylesheet = theme_stylesheet(theme)
        assert colors["text"] in stylesheet
        assert colors["window"] in stylesheet
