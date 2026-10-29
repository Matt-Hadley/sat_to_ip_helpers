import logging
from typing import Any

import requests

from sat_to_ip import http
from sat_to_ip.exceptions import IntegrationError

logger = logging.getLogger(__name__)


class ChannelsDVRClient:
    """HTTP client for the Channels DVR REST API.

    Pass an already-configured requests.Session (useful for testing with a
    mock/stub), or use ChannelsDVRClient.connect() in production code.
    """

    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url.rstrip("/")
        self.session = session

    @classmethod
    def connect(cls, base_url: str, sid: str | None = None) -> "ChannelsDVRClient":
        """Create a client, authenticated via the SID session cookie when one is given.

        Channels DVR doesn't ask for a login on the local network unless that is turned on.
        """
        session = http.session()
        if sid:
            session.cookies.set("SID", sid)
        return cls(base_url, session)

    def get_gracenote(self, station_list: str) -> list[dict[str, Any]]:
        """Fetch Gracenote station data for the given station list name."""
        url = f"{self.base_url}/dvr/guide/stations/{station_list}"
        try:
            resp = self.session.get(url)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise IntegrationError(f"Channels DVR: can't fetch station list {station_list!r}: {exc}") from exc
        except ValueError as exc:
            raise IntegrationError(f"Channels DVR: station list {station_list!r} is not JSON: {exc}") from exc
        # Channels DVR occasionally wraps the list in an outer list
        if isinstance(data, list) and data and isinstance(data[0], list):
            data = data[0]
        if not isinstance(data, list) or not all(isinstance(entry, dict) for entry in data):
            raise IntegrationError(f"Channels DVR: station list {station_list!r} is not a list of stations")
        return data

    def update_m3u_source(
        self,
        source_id: str,
        display_name: str,
        m3u_text: str,
        refresh: str = "24",
        limit: str = "8",
        numbering: str = "ignore",
        logos: str = "guide",
    ) -> None:
        """Replace the M3U text content of an existing Channels DVR source."""
        payload = {
            "name": display_name,
            "type": "MPEG-TS",
            "source": "Text",
            "url": "",
            "text": m3u_text,
            "refresh": refresh,
            "limit": limit,
            "satip": "",
            "numbering": numbering,
            "logos": logos,
            "xmltv_url": "",
            "xmltv_refresh": "",
        }
        try:
            resp = self.session.put(f"{self.base_url}/providers/m3u/sources/{source_id}", json=payload)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise IntegrationError(f"Channels DVR: can't update source {source_id!r}: {exc}") from exc
        logger.info(f"Updated Channels DVR source '{source_id}'")
