import json
import logging
import time

import requests

logger = logging.getLogger(__name__)


class OctopusClient:
    """HTTP client for the Octopus REST API.

    Pass an already-configured requests.Session (useful for testing with a
    mock/stub).  Use OctopusClient.login() in production code.
    """

    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url.rstrip("/")
        self.session = session

    @classmethod
    def login(cls, base_url: str, username: str, password: str) -> "OctopusClient":
        """Authenticate against /login and return an authenticated client."""
        session = requests.Session()
        resp = session.post(
            f"{base_url.rstrip('/')}/login",
            data={"login": username, "passwd": password, "lang": "en", "submit": "Login"},
            allow_redirects=True,
            verify=False,
        )
        resp.raise_for_status()
        if "session_id" not in session.cookies:
            raise RuntimeError("Login failed: no session_id cookie received")
        logger.info("Logged in to Octopus")
        return cls(base_url, session)

    # ------------------------------------------------------------------
    # Transponder upload & channel scan
    # ------------------------------------------------------------------

    def upload_transponders(self, payload: dict) -> None:
        """Upload a transponder list JSON to /channelsearch/uploadCustom."""
        json_bytes = json.dumps(payload, indent=2).encode("utf-8")
        resp = self.session.post(
            f"{self.base_url}/channelsearch/uploadCustom",
            files={"transponderlist": ("transponders.json", json_bytes, "application/json")},
            verify=False,
        )
        resp.raise_for_status()
        logger.info("Transponder list uploaded")

    def start_scan(self, positions: list[str]) -> None:
        """Trigger a SAT>IP channel scan for the given satellite positions.

        Positions like '28.2E' are normalised to '282E'.  Up to four tuner
        slots are supported; any remaining slots are set to 'disabled'.
        """
        target_s = [p.replace(".", "").replace("°", "").upper() for p in positions]
        while len(target_s) < 4:
            target_s.append("disabled")
        resp = self.session.post(
            f"{self.base_url}/startsearch-satip",
            json={"target-s": target_s},
            verify=False,
        )
        resp.raise_for_status()
        logger.info(f"Scan started: {target_s}")

    def poll_scan_until_complete(self, interval: int = 1, timeout: int = 600) -> dict:
        """Poll /status/octoscan-satip until the scan finishes.

        The Octopus returns 200 + JSON progress while scanning, and 404 once
        the scan is no longer running (its way of signalling completion).
        """
        logger.info("Polling scan status...")
        deadline = time.time() + timeout
        done_confirmations = 0
        while time.time() < deadline:
            ts = int(time.time() * 1000)
            resp = self.session.get(
                f"{self.base_url}/status/octoscan-satip",
                params={"_": ts},
                verify=False,
            )
            if resp.status_code == 404 or not resp.text.strip():
                done_confirmations += 1
                if done_confirmations >= 3:
                    logger.info("Scan complete")
                    return {}
                time.sleep(interval)
                continue
            done_confirmations = 0
            resp.raise_for_status()
            try:
                status = resp.json()
            except ValueError:
                # Octopus occasionally returns truncated JSON while the scan is initialising
                time.sleep(interval)
                continue
            raw_progress = status.get("Progress", None)
            progress = f"{float(raw_progress):.2f}" if raw_progress is not None else "?"
            found = status.get("Channels found", "?")
            source = status.get("Source List Name", "")
            logger.info(f"Scan in progress... {progress}% — {found} channels found ({source}) — retrying in {interval}s")
            time.sleep(interval)
        raise TimeoutError(f"Scan did not complete within {timeout}s")

    # ------------------------------------------------------------------
    # DMS channel management
    # ------------------------------------------------------------------

    def get_dms_channels(self) -> list[dict]:
        """Return the channels currently saved in the DMS."""
        resp = self.session.post(
            f"{self.base_url}/channels/data",
            params={"selected": "1"},
            verify=False,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", data) if isinstance(data, dict) else data

    def get_available_channels(self, ignore_ids: list[str]) -> list[dict]:
        """Return channels from the last scan that are not yet in the DMS."""
        resp = self.session.post(
            f"{self.base_url}/channels/data",
            data={"ignore": ",".join(ignore_ids)},
            verify=False,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", data) if isinstance(data, dict) else data

    def save_channels(self, channels: list[dict]) -> None:
        """Replace the DMS channel list with the provided list."""
        resp = self.session.post(
            f"{self.base_url}/channels/save",
            json=channels,
            verify=False,
        )
        resp.raise_for_status()
        logger.info(f"Saved {len(channels)} channels to DMS")

    def download_m3u(self) -> str:
        """Download the M3U playlist for the current DMS channel list."""
        resp = self.session.get(f"{self.base_url}/channels/m3u", verify=False)
        resp.raise_for_status()
        return resp.text
