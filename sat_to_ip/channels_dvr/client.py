import logging
from typing import Any
from urllib.parse import quote, urlsplit

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
            # only for the DVR's own host, so a redirect elsewhere never carries it.
            # The cookie jar matches a dotless host such as "localhost" as "localhost.local".
            host = urlsplit(base_url).hostname or ""
            session.cookies.set("SID", sid, domain=host if "." in host else f"{host}.local")
        return cls(base_url, session)

    def get_gracenote(self, station_list: str) -> list[dict[str, Any]]:
        """Fetch Gracenote station data for the given station list name."""
        url = f"{self.base_url}/dvr/guide/stations/{quote(station_list, safe='')}"
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

    def get_m3u_source(self, source_id: str) -> dict[str, Any]:
        """Return the settings of an existing custom M3U source (name, text, limit, numbering…)."""
        try:
            resp = self.session.get(self._source_url(source_id))
            if resp.status_code == 404:
                raise IntegrationError(
                    f"Channels DVR: source {source_id!r} doesn't exist; create it in Channels DVR first"
                )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise IntegrationError(f"Channels DVR: can't read source {source_id!r}: {exc}") from exc
        except ValueError as exc:
            raise IntegrationError(f"Channels DVR: source {source_id!r} is not JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise IntegrationError(f"Channels DVR: source {source_id!r} is not a settings object")
        return data

    def update_m3u_source(self, source_id: str, settings: dict[str, Any]) -> None:
        """Save a custom M3U source's settings exactly as given (Channels DVR replaces them all)."""
        try:
            resp = self.session.put(self._source_url(source_id), json=settings)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise IntegrationError(f"Channels DVR: can't update source {source_id!r}: {exc}") from exc
        logger.info(f"Updated Channels DVR source '{source_id}'")

    def refresh_m3u_source(self, source_id: str) -> None:
        """Ask Channels DVR to reload the source now instead of at its next scheduled refresh."""
        try:
            resp = self.session.post(f"{self._source_url(source_id)}/refresh")
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise IntegrationError(f"Channels DVR: can't refresh source {source_id!r}: {exc}") from exc

    def _source_url(self, source_id: str) -> str:
        return f"{self.base_url}/providers/m3u/sources/{quote(source_id, safe='')}"
