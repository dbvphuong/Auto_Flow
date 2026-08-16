import json
import os


DEFAULT_SYSTEM_CONFIG = {
    "show_chrome_when_running": False,
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
    except Exception:
        pass
    return config


def save_system_config(config):
    # Preserve settings owned by other views when one view updates only its section.
    merged = load_system_config()
    if isinstance(config, dict):
        merged.update(config)

    with open(get_system_config_path(), "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=4, ensure_ascii=False)
    return merged
