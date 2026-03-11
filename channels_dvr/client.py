import logging

import requests

logger = logging.getLogger(__name__)


class ChannelsDVRClient:
    """HTTP client for the Channels DVR REST API.

    Pass an already-configured requests.Session (useful for testing with a
    mock/stub).  Use the classmethod ChannelsDVRClient.with_sid() to create
    a session-cookie-authenticated instance in production code.
    """

    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url.rstrip("/")
        self.session = session

    @classmethod
    def with_sid(cls, host: str, port: int, sid: str) -> "ChannelsDVRClient":
        """Create a client authenticated via the SID session cookie."""
        session = requests.Session()
        session.cookies.set("SID", sid)
        return cls(f"http://{host}:{port}", session)

    def get_gracenote(self, station_list: str) -> list[dict]:
        """Fetch Gracenote station data for the given station list name."""
        resp = self.session.get(f"{self.base_url}/dvr/guide/stations/{station_list}")
        resp.raise_for_status()
        data = resp.json()
        # Channels DVR occasionally wraps the list in an outer list
        if isinstance(data, list) and data and isinstance(data[0], list):
            return data[0]
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
        resp = self.session.put(
            f"{self.base_url}/providers/m3u/sources/{source_id}",
            json=payload,
        )
        resp.raise_for_status()
        logger.info(f"Updated Channels DVR source '{source_id}'")
