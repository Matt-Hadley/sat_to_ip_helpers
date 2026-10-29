"""Channel-list files: one spec per line ('BBC One HD', 'BBC One HD@10773', 'all', 'tv', 'radio')."""

from pathlib import Path

from sat_to_ip.exceptions import ConfigError


def read_spec_file(path: Path) -> list[str]:
    """Return the specs in order; blank lines and lines starting with '#' are ignored."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError(f"Can't read channel list {path}: {exc}") from exc
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
