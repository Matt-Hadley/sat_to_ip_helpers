"""Channel spec filtering — match channels by name / name@frequency."""


def match_entry(entry: str, channels: list[dict], seen_ids: set) -> list[dict]:
    """Return channels matching a single spec entry ('Name' or 'Name@Frequency')."""
    if "@" in entry:
        name_part, freq_part = entry.split("@", 1)
        name_lower, freq = name_part.strip().lower(), freq_part.strip()
        return [
            ch
            for ch in channels
            if ch.get("name", "").lower() == name_lower
            and str(ch.get("frequency") or ch.get("freq", "")) == freq
            and ch.get("serviceid", id(ch)) not in seen_ids
        ]
    # No frequency — first match only; use Name@Frequency to be explicit
    name_lower = entry.lower()
    for ch in channels:
        if ch.get("name", "").lower() == name_lower and ch.get("serviceid", id(ch)) not in seen_ids:
            return [ch]
    return []


def filter_channels(channels: list[dict], spec: str) -> tuple[list[dict], list[str]]:
    """Return (matched_channels, unmatched_spec_entries).

    spec can be: all | video | audio | comma-separated 'Name' or 'Name@Frequency'
    """
    if spec == "all":
        return channels, []
    if spec in ("video", "audio"):
        return [ch for ch in channels if ch.get("type") == spec], []
    matches: list[dict] = []
    unmatched: list[str] = []
    seen_ids: set = set()
    for entry in (e.strip() for e in spec.split(",")):
        found = match_entry(entry, channels, seen_ids)
        if found:
            matches.extend(found)
            seen_ids.update(ch.get("serviceid", id(ch)) for ch in found)
        else:
            unmatched.append(entry)
    return matches, unmatched
