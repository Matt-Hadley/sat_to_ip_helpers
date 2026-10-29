import json
import logging
import time
from typing import Any, TypedDict, cast

import requests
import urllib3

from sat_to_ip import http
from sat_to_ip.exceptions import IntegrationError

logger = logging.getLogger(__name__)

# The Octopus NET serves a self-signed certificate, so its calls use verify=False; only imported when it's configured
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class OctopusChannel(TypedDict, total=False):
    """A channel as the Octopus API returns it (only the keys we read; the rest pass through untouched)."""

    name: str
    serviceid: str | int  # on the Octopus NET this includes the frequency, so it changes when a channel moves
    dvb_serviceid: int  # the broadcast service ID, which stays
    type: str  # "video" | "audio"
    frequency: int | float | str
    freq: int | float | str
    position: int


def _channel_list(data: Any) -> list[OctopusChannel]:
    return cast(list[OctopusChannel], data.get("data", data) if isinstance(data, dict) else data)


class OctopusClient:
    """HTTP client for the Octopus REST API.

    Pass an already-configured requests.Session (useful for testing with a
    mock/stub).  Use OctopusClient.login() in production code.
    """

    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url.rstrip("/")
        self.session = session

    def _call(self, method: str, path: str, doing: str, **kwargs: Any) -> requests.Response:
        """One request; network and HTTP failures become an IntegrationError that says what to check."""
        try:
            resp = self.session.request(method, f"{self.base_url}{path}", verify=False, timeout=30, **kwargs)
            resp.raise_for_status()
        except (requests.ConnectionError, requests.Timeout) as exc:
            why = "timed out" if isinstance(exc, requests.Timeout) else "couldn't connect"
            raise IntegrationError(
                f"Octopus NET at {self.base_url}: {why} while {doing}. "
                "Check [receiver] host and that the box is switched on and on the network."
            ) from exc
        except requests.HTTPError as exc:
            raise IntegrationError(
                f"Octopus NET at {self.base_url}: {doing} failed (HTTP {exc.response.status_code})"
            ) from exc
        return resp

    @staticmethod
    def _json(resp: requests.Response, doing: str) -> Any:
        try:
            return resp.json()
        except ValueError as exc:
            raise IntegrationError(f"Octopus NET: unexpected reply while {doing} (not JSON)") from exc

    @classmethod
    def login(cls, base_url: str, username: str, password: str) -> "OctopusClient":
        """Authenticate against /login and return an authenticated client."""
        client = cls(base_url, http.session())
        client._call(
            "POST",
            "/login",
            "logging in",
            data={"login": username, "passwd": password, "lang": "en", "submit": "Login"},
            allow_redirects=True,
        )
        if "session_id" not in client.session.cookies:
            raise IntegrationError(
                "Octopus NET: login was refused. Check [receiver] username and the password in its password_env variable."
            )
        logger.info("Logged in to Octopus")
        return client

    # ------------------------------------------------------------------
    # Transponder upload & channel scan
    # ------------------------------------------------------------------

    def upload_transponders(self, payload: dict[str, Any]) -> None:
        """Upload a transponder list JSON to /channelsearch/uploadCustom."""
        json_bytes = json.dumps(payload, indent=2).encode("utf-8")
        files = {"transponderlist": ("transponders.json", json_bytes, "application/json")}
        self._call("POST", "/channelsearch/uploadCustom", "uploading the transponder list", files=files)
        logger.info("Transponder list uploaded")

    def start_scan(self, positions: list[str]) -> None:
        """Trigger a SAT>IP channel scan for the given satellite positions.

        Positions like '28.2E' are normalised to '282E'.  Up to four tuner
        slots are supported; any remaining slots are set to 'disabled'.
        """
        target_s = [p.replace(".", "").replace("°", "").upper() for p in positions]
        while len(target_s) < 4:
            target_s.append("disabled")
        self._call("POST", "/startsearch-satip", "starting the scan", json={"target-s": target_s})
        logger.info(f"Scan started: {target_s}")

    def poll_scan_until_complete(self, interval: int = 1, timeout: int = 600) -> dict[str, Any]:
        """Poll /status/octoscan-satip until the scan finishes.

        The Octopus returns 200 + JSON progress while scanning, and 404 once
        the scan is no longer running (its way of signalling completion).
        """
        logger.info("Polling scan status...")
        deadline = time.time() + timeout
        done_confirmations = 0
        while time.time() < deadline:
            ts = int(time.time() * 1000)
            try:
                resp = self.session.get(
                    f"{self.base_url}/status/octoscan-satip",
                    params={"_": ts},
                    verify=False,
                    timeout=30,
                )
            except (
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ) as exc:
                logger.warning(f"Transient connection error during scan poll, retrying: {exc}")
                time.sleep(interval)
                continue
            if resp.status_code == 404 or not resp.text.strip():
                done_confirmations += 1
                if done_confirmations >= 3:
                    logger.info("Scan complete")
                    return {}
                time.sleep(interval)
                continue
            done_confirmations = 0
            if resp.status_code >= 400:
                raise IntegrationError(f"Octopus NET at {self.base_url}: scan status failed (HTTP {resp.status_code})")
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
            logger.info(
                f"Scan in progress... {progress}% — {found} channels found ({source}) — retrying in {interval}s"
            )
            time.sleep(interval)
        raise IntegrationError(f"Octopus NET: the scan didn't finish within {timeout}s; raise [receiver] scan_timeout")

    # ------------------------------------------------------------------
    # DMS channel management
    # ------------------------------------------------------------------

    def get_dms_channels(self) -> list[OctopusChannel]:
        """Return the channels currently saved in the DMS, in their saved order."""
        doing = "reading its saved channels"
        dms = _channel_list(self._json(self._call("POST", "/channels/data", doing, params={"selected": "1"}), doing))
        # The Octopus NET lists them sorted by name; `position` is the saved order (records without one go last)
        return sorted(dms, key=lambda ch: ch.get("position", len(dms)))

    def get_available_channels(self, ignore_ids: list[str]) -> list[OctopusChannel]:
        """Return channels from the last scan that are not yet in the DMS."""
        doing = "reading the channels it found"
        resp = self._call("POST", "/channels/data", doing, data={"ignore": ",".join(ignore_ids)})
        return _channel_list(self._json(resp, doing))

    def save_channels(self, channels: list[OctopusChannel]) -> None:
        """Replace the DMS channel list with the provided list."""
        self._call("POST", "/channels/save", "saving the channel list", json=channels)
        logger.info(f"Saved {len(channels)} channels to DMS")

    def download_m3u(self) -> str:
        """Download the M3U playlist for the current DMS channel list."""
        return self._call("GET", "/channels/m3u", "downloading its M3U").text

    def get_tuner_status(self) -> list[dict[str, Any]]:
        """Each input's Lock, Strength (0.001 dBm) and SNR (0.001 dB), as octoserve reports them."""
        doing = "reading its tuner status"
        data = self._json(self._call("GET", "/octoserve/tunerstatus.json", doing), doing)
        return cast(list[dict[str, Any]], data.get("TunerList", []) if isinstance(data, dict) else [])
