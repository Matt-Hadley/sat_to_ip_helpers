"""Name normalisation shared by every guide matcher."""

import re


def normalize_name(name: str) -> str:
    """Lowercase and strip everything but letters and digits: 'BBC One HD' -> 'bbconehd'."""
    return re.sub(r"[^a-z0-9]", "", name.lower())
