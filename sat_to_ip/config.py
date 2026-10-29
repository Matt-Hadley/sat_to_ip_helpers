"""Load and validate the TOML config. Every error names the key it is about.

Integrations read their own section through `Section`, which tracks the keys it hands out so
`finish()` can reject typos ("ouput_dir") instead of silently ignoring them.
"""

import os
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sat_to_ip import registry
from sat_to_ip.checks import CheckSettings
from sat_to_ip.exceptions import ConfigError
from sat_to_ip.interfaces import GuideSource, Publisher, Receiver

_MISSING: Any = object()


@dataclass
class Section:
    """One table of the config file, e.g. [receiver] or publishers[1]."""

    path: str
    data: Mapping[str, Any]
    base_dir: Path
    env: Mapping[str, str]
    name: str = ""
    _used: set[str] = field(default_factory=set)

    def error(self, key: str, message: str) -> ConfigError:
        return ConfigError(f"{self.path}.{key}: {message}")

    def _get(self, key: str, default: Any, kind: type | tuple[type, ...], kind_name: str) -> Any:
        self._used.add(key)
        if key not in self.data:
            if default is _MISSING:
                raise self.error(key, "required")
            return default
        value = self.data[key]
        if isinstance(value, bool) and bool not in (kind if isinstance(kind, tuple) else (kind,)):
            raise self.error(key, f"must be {kind_name}, got {value!r}")
        if not isinstance(value, kind):
            raise self.error(key, f"must be {kind_name}, got {value!r}")
        return value

    def get_str(self, key: str, default: Any = _MISSING) -> str:
        value = self._get(key, default, str, "a string")
        if isinstance(value, str) and not value.strip() and default is _MISSING:
            raise self.error(key, "must not be empty")
        return value  # type: ignore[no-any-return]

    def get_optional_str(self, key: str) -> str | None:
        value = self._get(key, None, str, "a string")
        return value or None

    def get_int(self, key: str, default: Any = _MISSING, *, minimum: int | None = None) -> int:
        value: int = self._get(key, default, int, "an integer")
        if minimum is not None and value < minimum:
            raise self.error(key, f"must be at least {minimum}, got {value}")
        return value

    def get_float(
        self, key: str, default: Any = _MISSING, *, minimum: float | None = None, maximum: float | None = None
    ) -> float:
        value = float(self._get(key, default, (int, float), "a number"))
        if minimum is not None and value < minimum:
            raise self.error(key, f"must be at least {minimum}, got {value}")
        if maximum is not None and value > maximum:
            raise self.error(key, f"must be at most {maximum}, got {value}")
        return value

    def get_bool(self, key: str, default: Any = _MISSING) -> bool:
        return self._get(key, default, bool, "true or false")  # type: ignore[no-any-return]

    def get_str_list(self, key: str, default: Any = _MISSING) -> list[str]:
        value = self._get(key, default, list, "a list of strings")
        if not all(isinstance(v, str) and v.strip() for v in value):
            raise self.error(key, f"must be a list of non-empty strings, got {value!r}")
        return list(value)

    def get_path(self, key: str, default: Any = _MISSING) -> Path:
        """A path; relative ones resolve against the config file's directory."""
        raw = self.get_str(key, default)
        return (self.base_dir / Path(raw).expanduser()).resolve()

    def get_optional_path(self, key: str) -> Path | None:
        raw = self.get_optional_str(key)
        return (self.base_dir / Path(raw).expanduser()).resolve() if raw else None

    def get_url(self, key: str, default: Any = _MISSING) -> str:
        value = self.get_str(key, default).rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise self.error(key, f"must start with http:// or https://, got {value!r}")
        return value

    def get_secret(self, env_key: str, *, required: bool = True) -> str | None:
        """Read the environment variable named by `env_key` (e.g. password_env = "OCTOPUS_PASSWORD")."""
        var = self.get_optional_str(env_key)
        if var is None:
            if required:
                raise self.error(env_key, "required: the name of the environment variable holding the secret")
            return None
        value = self.env.get(var)
        if not value and required:
            raise self.error(env_key, f"environment variable {var} is not set")
        return value or None

    def finish(self) -> None:
        unknown = sorted(set(self.data) - self._used - {"type"})
        if unknown:
            raise ConfigError(f"{self.path}: unknown key(s) {', '.join(unknown)}")


@dataclass(frozen=True)
class Config:
    receiver: Receiver
    guides: Mapping[str, GuideSource]
    publishers: tuple[Publisher, ...]
    checks: CheckSettings
    channels_file: Path | None
    path: Path


def load_config(path: Path, env: Mapping[str, str] | None = None) -> Config:
    """Read, validate and build everything the config describes. Does no network I/O."""
    env = os.environ if env is None else env
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Can't read config {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc

    base_dir = path.resolve().parent
    unknown = sorted(set(raw) - {"receiver", "selection", "checks", "guides", "publishers"})
    if unknown:
        raise ConfigError(f"{path}: unknown section(s) {', '.join(unknown)}")

    def section(sect_path: str, data: Any, name: str = "") -> Section:
        if not isinstance(data, dict):
            raise ConfigError(f"{sect_path}: must be a table")
        return Section(sect_path, data, base_dir, env, name)

    if "receiver" not in raw:
        raise ConfigError(f"{path}: missing [receiver] section")
    receiver = registry.build("receiver", section("receiver", raw["receiver"], "receiver"))

    sel = section("selection", raw.get("selection", {}))
    channels_file = sel.get_optional_path("channels")
    sel.finish()
    checks = _load_checks(section("checks", raw.get("checks", {})))
    guides = _load_guides(raw.get("guides", {}), section)
    publishers = _load_publishers(path, raw.get("publishers", []), section)
    _validate_publishers(publishers, guides)

    return Config(receiver, guides, tuple(publishers), checks, channels_file, path)


def _load_checks(chk: Section) -> CheckSettings:
    checks = CheckSettings(
        max_removed=chk.get_int("max_removed", CheckSettings.max_removed, minimum=0),
        min_guide_match=chk.get_float("min_guide_match", CheckSettings.min_guide_match, minimum=0, maximum=1),
    )
    chk.finish()
    return checks


def _load_guides(guides_raw: Any, section: Callable[..., Section]) -> dict[str, GuideSource]:
    if not isinstance(guides_raw, dict):
        raise ConfigError("guides: must be a table of named guide sources, e.g. [guides.freeview]")
    return {name: registry.build("guide", section(f"guides.{name}", data, name)) for name, data in guides_raw.items()}


def _load_publishers(path: Path, pubs_raw: Any, section: Callable[..., Section]) -> list[Publisher]:
    if not isinstance(pubs_raw, list) or not pubs_raw:
        raise ConfigError(f"{path}: add at least one [[publishers]] entry")
    publishers = []
    for i, data in enumerate(pubs_raw):
        sect = section(f"publishers[{i}]", data)
        sect.name = str(data.get("name") or data.get("type") or f"publisher{i}")
        sect.get_optional_str("name")  # marks it as known; every publisher may be named
        publishers.append(registry.build("publisher", sect))
    return publishers


def _validate_publishers(publishers: list[Publisher], guides: Mapping[str, GuideSource]) -> None:
    seen: set[str] = set()
    for i, pub in enumerate(publishers):
        if pub.name in seen:
            raise ConfigError(f"publishers[{i}].name: {pub.name!r} is used twice; give each publisher a unique name")
        seen.add(pub.name)
        if pub.guide is None:
            continue
        if pub.guide not in guides:
            raise ConfigError(f"publishers[{i}].guide: no guide source named {pub.guide!r} (have: {sorted(guides)})")
        kind = guides[pub.guide].kind
        if kind not in pub.accepts_guides:
            raise ConfigError(
                f"publishers[{i}].guide: {pub.name} can't use {kind} guides (accepts: {sorted(pub.accepts_guides)})"
            )
