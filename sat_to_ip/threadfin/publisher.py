"""Threadfin (and through it Plex): write the playlists and guide it reads as local files.

Threadfin re-reads them on its own update schedule. Set `api_url` to also have it re-read them
now through its API (POST /api/ with {"cmd": "update.xmltv"} then {"cmd": "update.m3u"}; the
latter rebuilds XEPG, which maps new channels by tvg-id). The API must be switched on in
Threadfin's settings. If its "API Authentication" is on too, give `username` and `password_env`
for a Threadfin user allowed to use the API: we log in first, and each reply carries the token
for the next command.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from sat_to_ip import http
from sat_to_ip.config import Section
from sat_to_ip.exceptions import IntegrationError
from sat_to_ip.file_output import write_lineup_files
from sat_to_ip.model import GuideMatch, Lineup, PublishResult


@dataclass
class ThreadfinPublisher:
    name: str
    output_dir: Path
    api_url: str | None = None
    username: str | None = None
    password: str | None = None
    guide: str | None = None
    accepts_guides: frozenset[str] = frozenset({"xmltv"})
    session: requests.Session = field(default_factory=http.session, repr=False)

    def publish(self, lineup: Lineup, guide: GuideMatch | None, *, dry_run: bool) -> PublishResult:
        out = write_lineup_files(self.output_dir, lineup, guide, dry_run=dry_run)
        if dry_run:
            return PublishResult(self.name, out.channels, f"dry run: wrote {out.directory}; no API update")
        if self.api_url is None:
            return PublishResult(
                self.name, out.channels, f"wrote {out.directory}; Threadfin picks it up on its schedule"
            )
        cmds = ["update.xmltv", "update.m3u"] if guide is not None and guide.xmltv is not None else ["update.m3u"]
        try:
            self._update(cmds)
        except (requests.RequestException, ValueError, IntegrationError) as exc:
            raise IntegrationError(
                f"{self.name}: files written to {out.directory}, but the API update failed: {exc}"
            ) from exc
        return PublishResult(self.name, out.channels, f"wrote {out.directory}; ran {', '.join(cmds)}")

    def _update(self, cmds: list[str]) -> None:
        token = None
        if self.username is not None:
            token = self._call({"cmd": "login", "username": self.username, "password": self.password}).get("token")
        for cmd in cmds:
            token = self._call({"cmd": cmd, **({"token": token} if token else {})}).get("token")

    def _call(self, body: dict[str, Any]) -> dict[str, Any]:
        resp = self.session.post(f"{self.api_url}/api/", json=body)
        if resp.status_code == 423:
            raise IntegrationError("the API is switched off in Threadfin's settings")
        resp.raise_for_status()
        # On a failed command Threadfin writes the error object and then the normal reply; read the first.
        reply, _ = json.JSONDecoder().raw_decode(resp.text)
        if not isinstance(reply, dict) or reply.get("status") is not True:
            error = reply.get("err") if isinstance(reply, dict) else reply
            raise IntegrationError(f"{body['cmd']}: {error or 'refused'}")
        return reply


def from_config(section: Section) -> ThreadfinPublisher:
    api_url = section.get_optional_str("api_url")
    username = section.get_optional_str("username")
    password = section.get_secret("password_env", required=username is not None)
    if username is None and "password_env" in section.data:
        raise section.error("username", "required when password_env is set")
    if username is not None and api_url is None:
        raise section.error("api_url", "required when username is set")
    return ThreadfinPublisher(
        name=section.name,
        output_dir=section.get_path("output_dir"),
        api_url=section.get_url("api_url") if api_url else None,
        username=username,
        password=password,
        guide=section.get_optional_str("guide"),
    )
