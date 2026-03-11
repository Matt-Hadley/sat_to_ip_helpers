import logging

import requests

from king_of_sat_scraper import DEFAULT_BASE_URL
from king_of_sat_scraper.scraper import KingOfSatScraper
from king_of_sat_scraper.transponder import Transponder

logger = logging.getLogger(__name__)


class KingOfSatClient:
    """HTTP client for KingOfSat.

    Pass an already-configured requests.Session (useful for testing with a
    mock/stub).  Use KingOfSatClient.create() in production code.
    """

    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url.rstrip("?")
        self.session = session

    @classmethod
    def create(cls, base_url: str = DEFAULT_BASE_URL) -> "KingOfSatClient":
        return cls(base_url, requests.Session())

    def _build_url(self, position: str, channel_filter: str, cl: str) -> str:
        return f"{self.base_url}?pos={position}" f"&standard=All&ordre=freq&filtre={channel_filter}&cl={cl}"

    def fetch_transponders(
        self,
        position: str,
        channel_filter: str = "Clear",
        cl: str = "eng",
    ) -> list[Transponder]:
        """Fetch and parse transponders for one satellite position.

        Args:
            position:       Satellite position, e.g. '28.2E'.
            channel_filter: 'Clear' (FTA only), 'All', or 'Encrypted'.
            cl:             Channel language filter, e.g. 'eng'.

        Returns:
            List of Transponder objects (empty if parse finds nothing).
        """
        url = self._build_url(position, channel_filter, cl)
        logger.info(f"Fetching {url}")
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        transponders = KingOfSatScraper(resp.text).parse_transponders()
        logger.info(f"{position}: {len(transponders)} transponders")
        return transponders
