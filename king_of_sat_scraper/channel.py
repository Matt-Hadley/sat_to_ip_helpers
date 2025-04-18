from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass
class Channel:
    channel_type: str  # Channel type, e.g., "v", "r", "feed"
    name: str  # Channel name, e.g., "BBC One HD"
    country: Optional[str]  # Country of origin, e.g., "United Kingdom"
    category: Optional[str]  # Genre/category, e.g., "News", "Sports"
    packages: List[str]  # Broadcasting providers, e.g., "BBC"
    encryption: str  # Encryption system, e.g., "None", "Viaccess", "Nagravision"
    sid: int  # Service ID (SID), unique identifier for the channel
    vpid: Optional[int]  # Video PID (Packet Identifier)
    apids: dict  # Audio PID {language: str, apid: int}
    pmt: Optional[int]  # Program Map Table PID
    pcr: Optional[int]  # PCR PID (Program Clock Reference)
    txt: Optional[int]  # Teletext PID
    last_updated: datetime  # Timestamp of last update

    def __str__(self):
        return (
            f"{self.name} [{self.country}] | {self.genre} by {self.provider} | "
            f"SID: {self.sid}, VPID: {self.vpid}, APID: {self.apid}, PCR: {self.pcr} | "
            f"Encryption: {self.encryption} | Last Updated: {self.last_updated.strftime('%Y-%m-%d %H:%M:%S')}"
        )
