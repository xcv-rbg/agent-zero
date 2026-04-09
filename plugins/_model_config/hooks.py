from copy import deepcopy

from helpers import plugins


def _merge_missing(target: dict, defaults: dict) -> dict:
    for key, value in defaults.items():
        if key not in target:
            target[key] = deepcopy(value)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _merge_missing(target[key], value)
    return target


def get_plugin_config(result=None, default=None, **kwargs):
    defaults = plugins.get_default_plugin_config("_model_config") or {}
    if isinstance(default, dict):
        result = default
    if not isinstance(result, dict):
        result = {}
    if isinstance(defaults, dict):
        result = _merge_missing(result, defaults)
    return result


def save_plugin_config(result=None, settings=None, **kwargs):
    if settings and isinstance(settings, dict):
        # Remove transient UI-only fields before persisting
        for section in ("chat_model", "utility_model", "war_model", "embedding_model"):
            if section in settings and isinstance(settings[section], dict):
                settings[section].pop("_kwargs_text", None)
                settings[section].pop("api_key", None)
    return settings
