# Design: one lineup, many DVRs

Status: accepted, 2026-10-29. Target version 0.2.0.

## Goal

Get the channels from a SAT>IP receiver into **Channels DVR, Jellyfin and Plex** (Plex through Threadfin) with guide data, from one unattended run, without the parts knowing about each other. Adding a receiver, a guide source or a DVR should mean adding one module and its tests, not editing the others.

### Requirements

- One run scans once, then publishes the same lineup to every configured target.
- Guide data: Gracenote (from Channels DVR) for Channels DVR; a public XMLTV feed (e.g. [Freeview-EPG](https://git.tdem.in/dp247/Freeview-EPG)) for Jellyfin and Threadfin. The feed URL is configuration, never code: free UK feeds move, and Ofcom's 2026 EPG regulations may change what is offered.
- Plex is reached through **Threadfin**, which presents an HDHomeRun tuner. Plex needs Plex Pass for Live TV. There is no Plex-specific code.
- Jellyfin and Threadfin run as containers on the same host and read files the pipeline writes. Jellyfin is then told to refresh its guide; Threadfin refreshes on its own schedule (optionally triggered through its API).
- Safety checks protect every target: refuse to shrink the receiver's saved channel list by more than N, refuse to publish an empty lineup, and refuse to publish to a target whose guide matched less than a minimum share of channels.
- One target failing never stops the others; the run still exits non-zero.
- No state kept between runs: every stage reads live data from the receiver.
- Secrets only from environment variables named in the config.

### Non-goals

- Third-party plugins via entry points. The registry is a plain dict; entry points can be added later without breaking anything.
- Plex's own API, a long-running HDHomeRun emulator, or reading EPG from the satellite (EIT).
- Managing Jellyfin/Threadfin installation or their one-time tuner setup (documented, done by hand once).

## Architecture

```
 Receiver ──lineup()──► Lineup ──► GuideSource(s) ──GuideMatch──► checks ──► Publisher(s)
   ▲ refresh()  (transponders + scan)                                      Channels DVR (API push)
   ▲ plan_selection() / apply_selection()  (channels.txt → saved list)     Jellyfin (files + refresh)
                                                                           Threadfin (files [+ API])
```

Only `pipeline.py` knows how the stages connect. Integrations are grouped by product, because one product can play two roles (Channels DVR is a guide source and a publisher):

```
sat_to_ip/
  model.py        Channel, Lineup, GuideRef, GuideMatch, SelectionPlan, PublishResult
  interfaces.py   Receiver, SupportsRefresh, SupportsSelection, GuideSource, Publisher (Protocols)
  config.py       load TOML, resolve *_env secrets, ConfigError with the key path
  registry.py     config "type" → builder, for receivers / guides / publishers
  pipeline.py     run stages, apply checks, isolate failures, build RunReport
  checks.py       removal check, empty-lineup check, guide-match check
  m3u.py          parse and render M3U (attributes, label, URL)
  files.py        atomic file writes
  http.py         requests.Session with timeouts and retries for idempotent calls
  names.py        normalize_name for fuzzy matching
  mappings.py     load Name→GuideId CSVs (built-in region + optional override file)
  mappings/<guide kind>/<region>/{tv,radio}.csv
  cli.py          sat-to-ip run | check-config
  file_output.py  tv.m3u / radio.m3u / guide.xml writer shared by file-reading DVRs
  kingofsat/      transponder scraper (any SAT>IP receiver can use it)
  octopus/        client, transponders, receiver.py, selection.py (Name / Name@MHz / all / tv / radio),
                  dms_editor.py (interactive selection)
  channels_dvr/   client, guide.py (Gracenote GuideSource), publisher.py
  xmltv/          guide.py (XMLTV GuideSource: download, gzip, streaming parse, match, filter)
  jellyfin/       publisher.py (files + Refresh Guide task)
  threadfin/      publisher.py (files + optional API update)
```

### Data model (`model.py`, frozen dataclasses)

- `Channel(key, name, kind, stream_url, number, frequency_mhz=None, service_id=None)`. `key` is stable and unique per receiver (`octopus:<serviceid>`); `kind` is `"tv"` or `"radio"`; `number` is the 1-based position in the receiver's saved list.
- `Lineup(channels: tuple[Channel, ...])` with `.tv`, `.radio`, `len()`.
- `GuideRef(guide_id, display_name=None, station_id=None)`: Gracenote call sign + station ID, or an XMLTV channel ID.
- `GuideMatch(source, kind, refs: Mapping[key, GuideRef], unmatched: tuple[Channel, ...], xmltv: bytes | None)`. `xmltv` is the feed filtered to the matched channels, for file publishers. `match_ratio` property.
- `SelectionPlan(current, proposed, unmatched_specs)`: channel `(key, name)` pairs; `removed` / `added` properties.
- `PublishResult(target, channels, detail)`.

### Interfaces (`interfaces.py`)

```python
class Receiver(Protocol):
    name: str
    def lineup(self) -> Lineup: ...

@runtime_checkable
class SupportsRefresh(Protocol):          # e.g. upload transponders + rescan
    def refresh(self) -> None: ...

@runtime_checkable
class SupportsSelection(Protocol):        # the receiver keeps a saved channel list
    def plan_selection(self, specs: Sequence[str]) -> SelectionPlan: ...
    def plan_selection_interactive(self) -> SelectionPlan | None: ...
    def apply_selection(self, plan: SelectionPlan) -> None: ...

class GuideSource(Protocol):
    name: str
    kind: str                              # "gracenote" | "xmltv"
    def match(self, lineup: Lineup) -> GuideMatch: ...

class Publisher(Protocol):
    name: str
    guide: str | None                      # name of the guide source it uses
    accepts_guides: frozenset[str]         # guide kinds it can use
    def publish(self, lineup: Lineup, guide: GuideMatch | None, *, dry_run: bool) -> PublishResult: ...
```

Optional receiver capabilities are separate Protocols, so a receiver with no scan or no saved list simply doesn't implement them, and the pipeline skips that stage with a warning.

## Configuration

TOML, loaded with `tomllib` (so Python ≥ 3.11). Each section's `type` picks a builder from `registry.py`; each integration validates its own section and raises `ConfigError("publishers[1].output_dir: required")`. Secret values are never in the file: a key ending in `_env` names the environment variable to read.

```toml
[receiver]
type = "octopus"
host = "octopus.local"
password_env = "OCTOPUS_PASSWORD"
positions = ["28.2E"]

[selection]
channels = "channels.txt"          # relative paths resolve against the config file

[checks]
max_removed = 5
min_guide_match = 0.5

[guides.gracenote]
type = "gracenote"
url = "http://localhost:8089"
station_list = "GBR-1000193-DEFAULT"
mappings = "uk"

[guides.freeview]
type = "xmltv"
url = "https://raw.githubusercontent.com/dp247/Freeview-EPG/master/epg.xml"
mappings = "freeview-epg"

[[publishers]]
type = "channels_dvr"
url = "http://localhost:8089"
guide = "gracenote"
tv_source = "OctopusTV"

[[publishers]]
type = "jellyfin"
url = "http://localhost:8096"
api_key_env = "JELLYFIN_API_KEY"
guide = "freeview"
output_dir = "/srv/jellyfin/livetv"

[[publishers]]
type = "threadfin"
guide = "freeview"
output_dir = "/srv/threadfin/files"
```

Config-time validation also checks that every publisher's `guide` exists and is a kind it accepts.

## Run flow and failure handling

`sat-to-ip run -c CONFIG [--stages refresh select publish] [--allow-removals] [--interactive] [--dry-run]`

1. **refresh** (if the receiver supports it): KingOfSat → upload transponders → scan. Failure aborts the run.
2. **select** (if supported): plan from `channels` (or the interactive editor), log the diff, refuse a plan that matches nothing, run the removal check (not for the interactive editor), apply. Dry run: plan and check, don't apply. Failure aborts the run.
3. **publish**:
   - `lineup = receiver.lineup()`; an empty lineup fails the stage.
   - Each guide source that a publisher uses runs once. If it errors or is below `min_guide_match`, it fails, and its publishers are reported failed without running.
   - Each publisher runs in isolation; an exception is recorded and the next one runs.
   - Unmatched channels are left out of that target's playlist (the current Channels DVR behaviour) and logged.
   - A target never gets an emptied playlist: if a kind (TV or radio) has channels but none matched, that target fails.
4. The `RunReport` lists every stage and target as ok / failed / skipped with a reason, logged as a summary. Exit code: 0 all ok, 1 any failure, 2 configuration error.

File publishers write `tv.m3u`, `radio.m3u` and `guide.xml` atomically (temp file + `os.replace`) so a reader never sees half a file. Dry run writes nothing outside a temp directory and calls no write APIs.

HTTP: every client uses `http.session()`: 30 s timeouts, retries with backoff on connection errors and 502/503/504 for GET only (never retry a write). The Octopus keeps `verify=False` (self-signed).

XMLTV feeds can be large: parsed with `iterparse` and cleared as it goes; gzip detected by magic bytes. The feed is untrusted: stdlib expat (no external entities; billion-laughs protection in expat ≥ 2.4.1, bundled with Python 3.11+).

## Testing

- Unit tests per module; HTTP stubbed with `MagicMock` sessions as today.
- Contract tests: a shared parametrised suite every `Publisher` passes (dry run makes no write calls; returns a `PublishResult`; leaves out unmatched channels), and one for `GuideSource`.
- Golden test: the Channels DVR publisher produces byte-for-byte the M3U the current enrichment produces for the same input.
- Pipeline tests with in-memory fake receiver / guides / publishers: stage skipping, abort rules, failure isolation, checks, exit codes.
- Config tests: valid file, every validation error names its key, `*_env` resolution, unknown `type`.
- mypy `--strict` on `sat_to_ip` in CI, coverage ≥ 95%, black/isort/ruff as now. CI matrix: Python 3.11 and 3.15.

## Migration

- Breaking: the `--steps` flags and `.pipeline_state/` are replaced by `run -c CONFIG [--stages …]`. Version 0.2.0.
- Mapping CSVs move to `sat_to_ip/mappings/gracenote/uk/` with header `Name,GuideId`.
- `channels.txt` is personal: it moves to the private deployment repo; `examples/channels.txt` and `examples/sat-to-ip.toml` stay here.
- server_config: the weekly workflow runs `sat-to-ip run -c channels/sat-to-ip.toml` with only the Channels DVR publisher enabled until the Jellyfin and Threadfin containers exist (separate follow-up).

## Build plan

1. **Core** (lead, sequential): `model`, `interfaces`, `config`, `registry`, `checks`, `pipeline`, `m3u`, `files`, `http`, `names`, `mappings`, with tests. Everything below codes against these.
2. **In parallel** (one agent each, separate files, own tests):
   - A. `octopus/receiver.py`: refresh, selection (spec + interactive editor), lineup from DMS + M3U.
   - B. `channels_dvr/guide.py` + `channels_dvr/publisher.py` (golden test against current output).
   - C. `xmltv/guide.py` + a starter UK mapping for the chosen free feed.
   - D. `jellyfin/publisher.py` + `threadfin/publisher.py`, APIs verified against their source code.
3. **Integrate** (lead): registry entries, `cli.py`, delete `steps.py` and the old `m3u/` package, README (setup per DVR, extending), example config, CI (mypy, 3.11).
4. **Review**: an independent reviewer reads the whole diff; findings fixed.
5. **Deploy config**: update the server_config workflow and add its config.
