from dataclasses import dataclass


@dataclass(frozen=True)
class Transponder:
    """The tuning details a receiver needs to scan one transponder."""

    satellite: str  # e.g. "Astra 2E"
    frequency: float  # MHz, e.g. 10773.25
    polarization: str  # "H", "V", "L" or "R"
    system: str  # e.g. "DVB-S2"
    modulation: str  # e.g. "8PSK"
    symbol_rate: int  # kS/s, e.g. 23000
