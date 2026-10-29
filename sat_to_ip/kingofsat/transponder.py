from dataclasses import dataclass


@dataclass
class Transponder:
    position: str  # e.g., "28.2°E"
    satellite: str  # e.g., "Astra 2E"
    frequency: float  # MHz, e.g., 10773
    polarization: str  # 'H' (horizontal) or 'V' (vertical)
    transponder_id: int  # e.g., 45
    beam: str  # e.g., "U.K."
    system: str  # e.g., "DVB-S2"
    modulation: str  # e.g., "8PSK"
    symbol_rate: int  # kS/s, e.g., 23000
    fec: str  # Forward error correction, e.g., "3/4"
    network_bitrate: str  # e.g., "50.1 Mb/s"
    nid: int  # Network ID, e.g., 2
    tid: int  # Transport Stream ID, e.g., 2045

    def __post_init__(self):
        valid_polarizations = {"H", "V"}
        if self.polarization.upper() not in valid_polarizations:
            raise ValueError(f"Invalid polarization: {self.polarization}. Must be 'H' or 'V'.")
        self.polarization = self.polarization.upper()
