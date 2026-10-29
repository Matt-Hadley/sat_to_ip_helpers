"""Channel names: normalisation shared by every guide matcher, and channels that share a broadcast slot."""

import re

# Channels that share one broadcast service and change name with the time of day: a scan shows whichever is
# on air. They're kept under the main (evening) name, so a daytime rescan doesn't rename them.
SHARED_SLOTS = {"CBBC HD": "BBC Three HD", "CBBC": "BBC Three", "CBeebies HD": "BBC Four HD", "CBeebies": "BBC Four"}


def slot_name(name: str) -> str:
    """The name a channel is kept under: 'CBBC HD' -> 'BBC Three HD'; any other name as it is."""
    return SHARED_SLOTS.get(name, name)


def normalize_name(name: str) -> str:
    """Lowercase and strip everything but letters and digits: 'BBC One HD' -> 'bbconehd'."""
    return re.sub(r"[^a-z0-9]", "", name.lower())
