import json
import os


CHROME_RUN_MODE_VISIBLE = "visible"
CHROME_RUN_MODE_MINIMIZED = "minimized"
CHROME_RUN_MODE_HEADLESS = "headless"
CHROME_RUN_MODES = {
    CHROME_RUN_MODE_VISIBLE,
    CHROME_RUN_MODE_MINIMIZED,
    CHROME_RUN_MODE_HEADLESS,
}


DEFAULT_SYSTEM_CONFIG = {
    "show_chrome_when_running": False,
    "chrome_run_mode": CHROME_RUN_MODE_MINIMIZED,
    "urban_vpn_order": ["DIRECT", "DE", "US", "GB"],
    "theme": "dark",
}


def get_system_config_path():
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.dirname(src_dir)
    data_dir = os.path.join(project_root, "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "config_system.json")


def load_system_config():
    config = DEFAULT_SYSTEM_CONFIG.copy()
    config_path = get_system_config_path()
    if not os.path.exists(config_path):
        return config

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            config.update(saved)
            # Backward compatibility with the former two-state checkbox.
            if "chrome_run_mode" not in saved:
                config["chrome_run_mode"] = (
                    CHROME_RUN_MODE_VISIBLE
                    if bool(saved.get("show_chrome_when_running", False))
                    else CHROME_RUN_MODE_MINIMIZED
                )
    except Exception:
        pass
    return config


def get_chrome_run_mode(config=None):
    """Return a supported Chrome mode, including migration from the old checkbox."""
    values = load_system_config() if config is None else config
    mode = values.get("chrome_run_mode") if isinstance(values, dict) else None
    if mode in CHROME_RUN_MODES:
        return mode
    if isinstance(values, dict) and bool(values.get("show_chrome_when_running", False)):
        return CHROME_RUN_MODE_VISIBLE
    return CHROME_RUN_MODE_MINIMIZED


def save_system_config(config):
    # Preserve settings owned by other views when one view updates only its section.
    merged = load_system_config()
    if isinstance(config, dict):
        merged.update(config)

    with open(get_system_config_path(), "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=4, ensure_ascii=False)
    return merged
