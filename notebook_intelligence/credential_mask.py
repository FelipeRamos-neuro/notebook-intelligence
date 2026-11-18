# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Keep stored API keys out of the capabilities response.

A key kept in config.json, whether typed in the settings panel or written by
hand, used to travel to the browser in clear text and fill a plain input. The
capabilities handler now sends ``SECRET_MASK`` in its place, and the config
handler maps the mask posted back onto the stored key. The panel can then
re-save its form untouched without overwriting the key it never saw.
"""

from __future__ import annotations

from typing import Any, Optional

SECRET_MASK = "********"


def mask_secret(value: Any) -> Any:
    """The mask for a non-empty string; anything else unchanged.

    An empty value stays empty so the panel can tell "no key" from "a key".
    """
    if isinstance(value, str) and value:
        return SECRET_MASK
    return value


def mask_settings_api_key(settings: Optional[dict]) -> Optional[dict]:
    """A copy of a Claude or ACP settings dict with its ``api_key`` masked."""
    if not isinstance(settings, dict) or not settings.get("api_key"):
        return settings
    result = dict(settings)
    result["api_key"] = mask_secret(result["api_key"])
    return result


def restore_masked_api_key(posted: Any, stored: Any) -> Any:
    """Swap a posted mask for the stored ``api_key``.

    ``stored`` without a key yields an empty one: a mask with nothing behind it
    must not be saved as though it were the key.
    """
    if not isinstance(posted, dict) or posted.get("api_key") != SECRET_MASK:
        return posted
    result = dict(posted)
    stored_key = stored.get("api_key", "") if isinstance(stored, dict) else ""
    result["api_key"] = stored_key if isinstance(stored_key, str) else ""
    return result


def mask_model_properties(models: list[dict]) -> list[dict]:
    """Copies of the model dicts with each secret property's value masked."""
    masked = []
    for model in models:
        properties = model.get("properties")
        if not isinstance(properties, list):
            masked.append(model)
            continue
        masked.append(
            {
                **model,
                "properties": [
                    {**prop, "value": mask_secret(prop.get("value"))}
                    if prop.get("secret")
                    else prop
                    for prop in properties
                ],
            }
        )
    return masked


def restore_masked_model_properties(posted: Any, stored: Any) -> Any:
    """Swap posted property masks for the values stored for the same model.

    Only the model the config already names can legitimately hold a key, since
    the server loads property values for the selected model alone. A mask for
    any other model has no key behind it and becomes empty.
    """
    if not isinstance(posted, dict) or not isinstance(posted.get("properties"), list):
        return posted
    same_model = (
        isinstance(stored, dict)
        and stored.get("provider") == posted.get("provider")
        and stored.get("model") == posted.get("model")
    )
    stored_values = {}
    if same_model and isinstance(stored.get("properties"), list):
        stored_values = {
            prop.get("id"): prop.get("value", "")
            for prop in stored["properties"]
            if isinstance(prop, dict)
        }
    properties = []
    for prop in posted["properties"]:
        if isinstance(prop, dict) and prop.get("value") == SECRET_MASK:
            prop = {**prop, "value": stored_values.get(prop.get("id"), "")}
        properties.append(prop)
    return {**posted, "properties": properties}
