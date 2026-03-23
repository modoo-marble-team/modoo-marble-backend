from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

PLACEHOLDER_PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\$")


def render_card_text(
    card: Mapping[str, Any],
    *,
    template_key: str = "description",
    variables: Mapping[str, Any] | None = None,
) -> str:
    template = card.get(template_key)
    if not isinstance(template, str) or not template:
        return ""

    resolved_values = _build_template_values(card, variables)
    return PLACEHOLDER_PATTERN.sub(
        lambda match: resolved_values.get(match.group(1), ""),
        template,
    )


def _build_template_values(
    card: Mapping[str, Any],
    variables: Mapping[str, Any] | None,
) -> dict[str, str]:
    resolved = {
        str(key): _stringify(value)
        for key, value in card.items()
        if isinstance(key, str)
    }

    if "value" not in resolved:
        for candidate in ("amount", "duration", "multiplier"):
            if candidate in card:
                resolved["value"] = _stringify(card.get(candidate))
                break
        else:
            resolved["value"] = ""

    if variables:
        for key, value in variables.items():
            resolved[str(key)] = _stringify(value)

    return resolved


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
