import os
import models
from helpers import plugins, files
from helpers import yaml as yaml_helper
from helpers.providers import get_providers

PRESETS_FILE = "presets.yaml"
DEFAULT_PRESETS_FILE = "default_presets.yaml"
LOCAL_PROVIDERS = {"ollama", "lm_studio"}
LOCAL_EMBEDDING = {"huggingface"}


def _get_presets_path() -> str:
    """Return the path to the user's global presets file (usr/plugins/_model_config/presets.yaml)."""
    return files.get_abs_path(files.USER_DIR, files.PLUGINS_DIR, "_model_config", PRESETS_FILE)


def _get_default_presets_path() -> str:
    """Return the path to the default presets file shipped with the plugin."""
    plugin_dir = plugins.find_plugin_dir("_model_config")
    return files.get_abs_path(plugin_dir, DEFAULT_PRESETS_FILE) if plugin_dir else ""


def get_config(agent=None, project_name=None, agent_profile=None):
    """Get the full model config dict for the given agent/scope."""
    return plugins.get_plugin_config(
        "_model_config",
        agent=agent,
        project_name=project_name,
        agent_profile=agent_profile,
    ) or {}


def get_presets() -> list:
    """Get global model presets list (not scoped to project/agent)."""
    path = _get_presets_path()
    if files.exists(path):
        data = yaml_helper.loads(files.read_file(path))
        if isinstance(data, list):
            return data
    # Fall back to defaults bundled with the plugin
    default_path = _get_default_presets_path()
    if default_path and files.exists(default_path):
        data = yaml_helper.loads(files.read_file(default_path))
        if isinstance(data, list):
            return data
    return []


def save_presets(presets: list) -> None:
    """Save the global presets list."""
    path = _get_presets_path()
    files.write_file(path, yaml_helper.dumps(presets))


def reset_presets() -> list:
    """Delete user presets so get_presets() falls back to bundled defaults. Returns the default presets."""
    path = _get_presets_path()
    if os.path.exists(path):
        os.remove(path)
    return get_presets()


def get_preset_by_name(name: str) -> dict | None:
    """Find a preset by name from the global presets list."""
    for p in get_presets():
        if p.get("name") == name:
            return p
    return None


def _resolve_override(agent) -> dict | None:
    """Resolve the active per-chat override config dict.
    Supports both raw override dicts and preset-based overrides.
    Returns None if no override is active or if override is not allowed."""
    if not agent:
        return None
    if not is_chat_override_allowed(agent):
        return None
    override = agent.context.get_data("chat_model_override")
    if not override:
        return None

    # If this is a preset reference, resolve it
    if "preset_name" in override:
        preset = get_preset_by_name(override["preset_name"])
        if not preset:
            return None
        return preset

    return override


def get_chat_model_config(agent=None) -> dict:
    """Get chat model config, with per-chat override if active."""
    override = _resolve_override(agent)
    if override:
        # Preset has a nested 'chat' key; raw override is flat
        chat_cfg = override.get("chat", override)
        if chat_cfg.get("provider") or chat_cfg.get("name"):
            return chat_cfg
    cfg = get_config(agent)
    return cfg.get("chat_model", {})


def get_utility_model_config(agent=None) -> dict:
    """Get utility model config, with per-chat override if active."""
    override = _resolve_override(agent)
    if override:
        util_cfg = override.get("utility", {})
        if util_cfg.get("provider") or util_cfg.get("name"):
            return util_cfg
    cfg = get_config(agent)
    return cfg.get("utility_model", {})


def _get_agent_scope(agent=None) -> tuple[str, str]:
    """Return (project_name, agent_profile) for current runtime agent."""
    project_name = ""
    agent_profile = ""

    if not agent:
        return project_name, agent_profile

    try:
        from helpers import projects

        project_name = projects.get_context_project_name(agent.context) or ""
    except Exception:
        project_name = ""

    try:
        agent_profile = getattr(getattr(agent, "config", None), "profile", "") or ""
    except Exception:
        agent_profile = ""

    return project_name, agent_profile


def _find_explicit_war_scope_override(agent=None) -> tuple[dict, bool]:
    """Find the first explicit war_model key in scope order.

    Returns:
      (war_dict, True)  if a scope explicitly defines war_model
      ({}, False)       if no scope defines war_model key at all

    Important: we inspect raw config files so we can distinguish between:
    - key absent (inherit from broader scope)
    - key present but blank (intentional inherit-main override at this scope)
    """
    project_name, agent_profile = _get_agent_scope(agent)
    scoped_configs = plugins.find_plugin_assets(
        plugins.CONFIG_FILE_NAME,
        plugin_name="_model_config",
        project_name=project_name,
        agent_profile=agent_profile,
        only_first=False,
    )

    for config_file in scoped_configs:
        path = config_file.get("path", "")
        if not path:
            continue
        try:
            cfg = files.read_file_json(path) or {}
        except Exception:
            continue
        if not isinstance(cfg, dict):
            continue
        if "war_model" not in cfg:
            continue

        war_cfg = cfg.get("war_model")
        return (war_cfg if isinstance(war_cfg, dict) else {}), True

    return {}, False


def get_war_model_config(agent=None) -> dict:
    """Get War Room model config.

    Works identically to get_utility_model_config but falls back to the main
    chat model when no dedicated War Room provider/name is configured.
    """
    explicit_war, has_explicit_war = _find_explicit_war_scope_override(agent)
    if has_explicit_war:
        war = explicit_war
    else:
        cfg = get_config(agent)
        war = cfg.get("war_model", {})

    if not isinstance(war, dict):
        war = {}

    main = get_chat_model_config(agent)

    # If no dedicated War Room model is configured, inherit Main entirely.
    if not (war.get("provider") or war.get("name")):
        return main

    # Dedicated War Room model: inherit missing connection fields from Main,
    # so users can override model/provider without duplicating every setting.
    merged = dict(main) if isinstance(main, dict) else {}
    if isinstance(war, dict):
        merged.update(war)

    if not merged.get("provider"):
        merged["provider"] = main.get("provider", "")
    if not merged.get("name"):
        merged["name"] = main.get("name", "")
    if not merged.get("api_base") and main.get("api_base"):
        merged["api_base"] = main.get("api_base", "")
    if not merged.get("api_key") and main.get("api_key"):
        merged["api_key"] = main.get("api_key", "")

    main_kwargs = main.get("kwargs", {}) if isinstance(main, dict) else {}
    merged_kwargs = merged.get("kwargs", {}) if isinstance(merged, dict) else {}
    if isinstance(main_kwargs, dict):
        if isinstance(merged_kwargs, dict):
            kw = dict(main_kwargs)
            kw.update(merged_kwargs)
            merged["kwargs"] = kw
        elif not merged_kwargs:
            merged["kwargs"] = dict(main_kwargs)

    return merged


def get_embedding_model_config(agent=None) -> dict:
    """Get embedding model config."""
    cfg = get_config(agent)
    return cfg.get("embedding_model", {})


def build_war_model(agent=None):
    """Build and return a LiteLLMChatWrapper for War Room expert calls."""
    cfg = get_war_model_config(agent)
    mc = build_model_config(cfg, models.ModelType.CHAT)
    return models.get_chat_model(
        mc.provider, mc.name, model_config=mc, **mc.build_kwargs()
    )


def get_war_model_display(agent=None) -> str:
    """Return a human-readable 'provider/name' string for the War Room model."""
    war, has_explicit_war = _find_explicit_war_scope_override(agent)
    if has_explicit_war and (war.get("provider") or war.get("name")):
        resolved = get_war_model_config(agent)
        return f"{resolved.get('provider', '')}/{resolved.get('name', '')}"
    main = get_chat_model_config(agent)
    return f"(inherits Main) {main.get('provider', '')}/{main.get('name', '')}"


def is_chat_override_allowed(agent=None) -> bool:
    """Check if per-chat model override is enabled."""
    cfg = get_config(agent)
    return bool(cfg.get("allow_chat_override", False))


def get_ctx_history(agent=None) -> float:
    """Get the chat model context history ratio."""
    cfg = get_chat_model_config(agent)
    return float(cfg.get("ctx_history", 0.7))


def get_ctx_input(agent=None) -> float:
    """Get the utility model context input ratio."""
    cfg = get_utility_model_config(agent)
    return float(cfg.get("ctx_input", 0.7))


def _normalize_kwargs(kwargs: dict) -> dict:
    """Convert string values that are valid numbers to numeric types."""
    result = {}
    for key, value in kwargs.items():
        if isinstance(value, str):
            try:
                result[key] = int(value)
            except ValueError:
                try:
                    result[key] = float(value)
                except ValueError:
                    result[key] = value
        else:
            result[key] = value
    return result


def build_model_config(cfg: dict, model_type: models.ModelType) -> models.ModelConfig:
    """Build a ModelConfig from a config dict section."""
    return models.ModelConfig(
        type=model_type,
        provider=cfg.get("provider", ""),
        name=cfg.get("name", ""),
        api_key=cfg.get("api_key", ""),
        api_base=cfg.get("api_base", ""),
        ctx_length=int(cfg.get("ctx_length", 0)),
        vision=bool(cfg.get("vision", False)),
        limit_requests=int(cfg.get("rl_requests", 0)),
        limit_input=int(cfg.get("rl_input", 0)),
        limit_output=int(cfg.get("rl_output", 0)),
        kwargs=_normalize_kwargs(cfg.get("kwargs", {})),
    )


def build_chat_model(agent=None):
    """Build and return a LiteLLMChatWrapper from config."""
    cfg = get_chat_model_config(agent)
    mc = build_model_config(cfg, models.ModelType.CHAT)
    return models.get_chat_model(
        mc.provider, mc.name, model_config=mc, **mc.build_kwargs()
    )


def build_utility_model(agent=None):
    """Build and return a LiteLLMChatWrapper for utility tasks."""
    cfg = get_utility_model_config(agent)
    mc = build_model_config(cfg, models.ModelType.CHAT)
    return models.get_chat_model(
        mc.provider, mc.name, model_config=mc, **mc.build_kwargs()
    )


def build_embedding_model(agent=None):
    """Build and return an embedding model wrapper."""
    cfg = get_embedding_model_config(agent)
    mc = build_model_config(cfg, models.ModelType.EMBEDDING)
    return models.get_embedding_model(
        mc.provider, mc.name, model_config=mc, **mc.build_kwargs()
    )


def get_embedding_model_config_object(agent=None) -> models.ModelConfig:
    """Get a ModelConfig object for embeddings (needed by memory plugin)."""
    cfg = get_embedding_model_config(agent)
    return build_model_config(cfg, models.ModelType.EMBEDDING)


def get_chat_providers():
    """Get list of chat providers for UI dropdowns."""
    return get_providers("chat")


def get_embedding_providers():
    """Get list of embedding providers for UI dropdowns."""
    return get_providers("embedding")


def has_provider_api_key(provider: str, configured_api_key: str = "") -> bool:
    configured_value = (configured_api_key or "").strip()
    if configured_value and configured_value != "None":
        return True

    api_key = models.get_api_key(provider.lower())
    return bool(api_key and api_key.strip() and api_key != "None")


def get_missing_api_key_providers(agent=None) -> list[dict]:
    """Check which configured providers are missing API keys."""
    cfg = get_config(agent)
    missing = []

    checks = [
        ("Chat Model", cfg.get("chat_model", {})),
        ("Utility Model", cfg.get("utility_model", {})),
        *(
            [("War Room Model", cfg.get("war_model", {}))]
            if cfg.get("war_model", {}).get("provider")
            else []
        ),
        ("Embedding Model", cfg.get("embedding_model", {})),
    ]

    for label, model_cfg in checks:
        provider = model_cfg.get("provider", "")
        if not provider:
            continue
        provider_lower = provider.lower()
        if provider_lower in LOCAL_PROVIDERS:
            continue
        if label == "Embedding Model" and provider_lower in LOCAL_EMBEDDING:
            continue

        if not has_provider_api_key(provider_lower, model_cfg.get("api_key", "")):
            missing.append({"model_type": label, "provider": provider})

    return missing
