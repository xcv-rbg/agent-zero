# PATCH: helpers/settings.py — War Room Model Configuration
#
# APPLY THESE THREE CHANGES to helpers/settings.py
# ─────────────────────────────────────────────────

# CHANGE 1 ── inside class Settings(TypedDict), add this field:
#
#     war_room_chat_model: dict
#     # War Room model for tools/think.py expert panel calls.
#     # Empty dict = inherit main chat model. Supported keys:
#     # provider (str), name (str), api_key (str), api_base (str),
#     # ctx_length (int), kwargs (dict e.g. {"temperature": 0.1})


# CHANGE 2 ── in your default settings initialiser, add:
#
#     "war_room_chat_model": {},


# CHANGE 3 ── in the WebUI settings builder, add this SettingsSection:
#
#  SettingsSection(
#    id="war_room_model",
#    title="War Room Model",
#    description=(
#      "Dedicated model for the War Room expert panel (think tool). "
#      "Use a stricter, code-focused model here. "
#      "Leave all fields empty to inherit the main chat model."
#    ),
#    fields=[
#      SettingsField(id="war_room_chat_model.provider", title="Provider",
#        description="e.g. openai, anthropic, ollama", type="input",
#        value=settings.get("war_room_chat_model",{}).get("provider","")),
#      SettingsField(id="war_room_chat_model.name", title="Model name",
#        description="e.g. gpt-4.1, claude-opus-4-5, deepseek-coder", type="input",
#        value=settings.get("war_room_chat_model",{}).get("name","")),
#      SettingsField(id="war_room_chat_model.api_key", title="API Key",
#        description="Leave empty to reuse main provider key", type="password",
#        value=settings.get("war_room_chat_model",{}).get("api_key","")),
#      SettingsField(id="war_room_chat_model.api_base", title="API Base URL",
#        description="Optional - for local/proxy endpoints", type="input",
#        value=settings.get("war_room_chat_model",{}).get("api_base","")),
#      SettingsField(id="war_room_chat_model.ctx_length", title="Context length",
#        description="0 = auto", type="number",
#        value=settings.get("war_room_chat_model",{}).get("ctx_length",0)),
#    ],
#  ),


# CHANGE 4 ── in save/update settings, expand dotted nested keys:
#
#  war_room_model = {}
#  for k in ("provider","name","api_key","api_base","ctx_length"):
#      flat = f"war_room_chat_model.{k}"
#      if flat in incoming_settings:
#          war_room_model[k] = incoming_settings.pop(flat)
#  if war_room_model:
#      incoming_settings["war_room_chat_model"] = war_room_model


# VERIFY after patching:
#   python -c "from helpers import settings; s=settings.get_settings(); print(s.get('war_room_chat_model','NOT FOUND'))"
# Expected output: {}
