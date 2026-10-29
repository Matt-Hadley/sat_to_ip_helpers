"""Config `type` → builder. To add a product, write `from_config(section)` and add a line here.

Builders are imported lazily, so one integration's import error can't break the others.
"""

import importlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sat_to_ip.config import Section

BUILDERS: dict[str, dict[str, str]] = {
    "receiver": {
        "octopus": "sat_to_ip.octopus.receiver:from_config",
    },
    "guide": {
        "gracenote": "sat_to_ip.channels_dvr.guide:from_config",
        "xmltv": "sat_to_ip.xmltv.guide:from_config",
    },
    "publisher": {
        "channels_dvr": "sat_to_ip.channels_dvr.publisher:from_config",
        "jellyfin": "sat_to_ip.jellyfin.publisher:from_config",
        "threadfin": "sat_to_ip.threadfin.publisher:from_config",
    },
}


def build(role: str, section: "Section") -> Any:
    type_name = section.get_str("type")
    table = BUILDERS[role]
    if type_name not in table:
        raise section.error("type", f"unknown {role} type {type_name!r} (known: {', '.join(sorted(table))})")
    module_name, _, attr = table[type_name].partition(":")
    builder: Callable[["Section"], Any] = getattr(importlib.import_module(module_name), attr)
    built = builder(section)
    section.finish()
    return built
