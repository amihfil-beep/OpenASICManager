"""Conservative stock ANTMINER model identification.

Model recognition is read-only and never grants control capability.
The exact firmware/API contract still requires physical validation.
"""

import re


_AIR_MODEL = re.compile(
    r"^(?:antminer\s+)?(T21|S21(?:\s+Pro)?)$",
    re.IGNORECASE,
)


def normalize_stock_model(value):
    """Canonicalize only the exact T21, S21 and S21 Pro air models."""
    if not isinstance(value, str):
        return None

    name = " ".join(value.split())
    if not name:
        return None

    match = _AIR_MODEL.fullmatch(name)
    if match:
        family = match.group(1).upper()
        if family == "S21 PRO":
            return "Antminer S21 Pro"
        return "Antminer " + family

    # Do not silently relabel Hydro, Immersion, XP, S21+, etc.
    if name.casefold().startswith("antminer "):
        return name

    return None


def stock_model_from_responses(system_info=None, stats_items=None):
    """Use explicit API-reported model names, never firmware guesses."""
    if isinstance(system_info, dict):
        for key in ("minertype", "miner_type", "model"):
            model = normalize_stock_model(system_info.get(key))
            if model:
                return model

    if isinstance(stats_items, list):
        for entry in stats_items:
            if not isinstance(entry, dict):
                continue
            for key in ("Type", "type", "minertype", "miner_type"):
                model = normalize_stock_model(entry.get(key))
                if model:
                    return model

    return None


__all__ = ("normalize_stock_model", "stock_model_from_responses")
