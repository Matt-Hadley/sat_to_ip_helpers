"""Jellyfin: write the playlists and guide its M3U tuner and XMLTV provider read, then refresh its guide.

Refreshing runs Jellyfin's "Refresh Guide" scheduled task (Key "RefreshGuide") through
GET /ScheduledTasks and POST /ScheduledTasks/Running/{id}. Both need an administrator, which every
Jellyfin API key is. Jellyfin caches an XMLTV file for an hour, so a refresh within an hour of the
last one may still use the previous guide.
"""

from dataclasses import dataclass, field
from pathlib import Path

import requests

from sat_to_ip import http
from sat_to_ip.config import Section
from sat_to_ip.exceptions import IntegrationError
from sat_to_ip.file_output import write_lineup_files
from sat_to_ip.model import GuideMatch, Lineup, PublishResult

REFRESH_GUIDE_KEY = "RefreshGuide"


@dataclass
class JellyfinPublisher:
    name: str
    output_dir: Path
    url: str = "http://localhost:8096"
    api_key: str | None = None
    refresh: bool = True
    guide: str | None = None
    accepts_guides: frozenset[str] = frozenset({"xmltv"})
    session: requests.Session = field(default_factory=http.session, repr=False)

    def publish(self, lineup: Lineup, guide: GuideMatch | None, *, dry_run: bool) -> PublishResult:
        out = write_lineup_files(self.output_dir, lineup, guide, dry_run=dry_run)
        if dry_run:
            return PublishResult(self.name, out.channels, f"dry run: wrote {out.directory}; no guide refresh")
        if not self.refresh:
            return PublishResult(self.name, out.channels, f"wrote {out.directory}; guide refresh off")
        try:
            self._start_refresh_guide()
        except (requests.RequestException, ValueError, IntegrationError) as exc:
            raise IntegrationError(
                f"{self.name}: files written to {out.directory}, but starting Refresh Guide failed: {exc}"
            ) from exc
        return PublishResult(self.name, out.channels, f"wrote {out.directory}; started Refresh Guide")

    def _start_refresh_guide(self) -> None:
        headers = {"Authorization": f'MediaBrowser Token="{self.api_key}"'}
        resp = self.session.get(f"{self.url}/ScheduledTasks", headers=headers)
        resp.raise_for_status()
        task = next((t for t in resp.json() if t.get("Key") == REFRESH_GUIDE_KEY), None)
        if task is None:
            raise IntegrationError(f"no scheduled task with Key {REFRESH_GUIDE_KEY!r} (is Live TV installed?)")
        self.session.post(f"{self.url}/ScheduledTasks/Running/{task['Id']}", headers=headers).raise_for_status()


def from_config(section: Section) -> JellyfinPublisher:
    refresh = section.get_bool("refresh", True)
    return JellyfinPublisher(
        name=section.name,
        output_dir=section.get_path("output_dir"),
        url=section.get_url("url", "http://localhost:8096"),
        api_key=section.get_secret("api_key_env", required=refresh),
        refresh=refresh,
        guide=section.get_optional_str("guide"),
    )
