# sat_to_ip_helpers

Automates the end-to-end workflow of getting UK Free-to-Air satellite channels into [Channels DVR](https://getchannels.com/) with full EPG guide data.

## Background

The setup this targets:

```
Satellite dish
     │
     ▼
Digital Devices Octopus NET  ←── SAT>IP server: tuner over your LAN
     │  (e.g. http://octopus.local)
     ▼
Channels DVR                 ←── DVR/streaming server with EPG guide
     │  (e.g. http://localhost:8089)
     ▼
Apple TV / phone / browser   ←── client apps
```

**The problem this solves:** To watch a satellite channel in Channels DVR you need to:

1. Tell the Octopus which satellite transponders to scan
2. Run a scan to find channels on those transponders
3. Add discovered channels to the Octopus DMS (its internal channel list)
4. Download the Octopus M3U playlist (one stream URL per channel)
5. Enrich that M3U with Gracenote station IDs so Channels DVR can match streams to EPG data
6. Push the enriched M3U into Channels DVR as a source

Previously this was 6 manual steps. This project automates all of them.

---

## Requirements

- Python 3.10+
- Dependencies:

```bash
pip install -r requirements.txt
```

---

## Quick start — run the full pipeline

```bash
export OCTOPUS_PASSWORD="your_octopus_password"
export CHANNELS_DVR_SID="your_channels_dvr_sid_cookie"

python pipeline.py --octopus-host octopus.local \
  --source-id <your-tv-source-id> \
  --source-id-radio <your-radio-source-id>
```

This runs all 8 steps in sequence. Output from each step is saved to `.pipeline_state/` so you can re-run individual steps without repeating earlier ones.

### Getting the Channels DVR SID cookie

The SID is a long-lived session cookie. Grab it from your browser:

1. Open Channels DVR in your browser (e.g. `http://localhost:8089`)
2. Open DevTools → Application → Cookies
3. Copy the value of the `SID` cookie

---

## Pipeline steps

| # | What it does |
|---|---|
| 1 | Scrape transponder frequencies from [KingOfSat](https://en.kingofsat.net/) |
| 2 | Upload the transponder list to the Octopus NET |
| 3 | Trigger a channel scan on the Octopus and wait for it to finish |
| 4 | Add all discovered channels to the Octopus DMS |
| 5 | Download the Octopus M3U playlist and split into separate TV and radio playlists |
| 6 | Fetch the Gracenote station list from Channels DVR |
| 7 | Enrich the M3U with Gracenote `channel-id` and `tvc-guide-stationid` tags |
| 8 | Push the enriched TV and radio M3Us into Channels DVR as separate sources |

Step 5 downloads the full Octopus M3U and splits it into two playlists based on the DMS channel `type` field (`video` → TV, `audio` → radio). Steps 7 and 8 then enrich and push each playlist independently, so you can have separate Channels DVR sources for TV and radio.

### Run specific steps

State from each step is saved to disk, so you can run any subset. Each step tells you which parameters it needs:

```bash
# Trigger an Octopus scan only — needs Octopus connection + positions
python pipeline.py --steps 3 --octopus-host octopus.local --positions 28.2E

# Re-run just the enrichment and push using the built-in UK mappings
python pipeline.py --steps 7 8 --source-id <your-tv-source-id> --source-id-radio <your-radio-source-id> --mapping-region uk

# Just scrape and upload new transponders, without touching channels
python pipeline.py --steps 1 2 --octopus-host octopus.local
```

---

## Configuration

| Env var | Description |
|---|---|
| `OCTOPUS_PASSWORD` | Octopus NET login password |
| `CHANNELS_DVR_SID` | Channels DVR SID session cookie |

```
python pipeline.py --help

Step 1 — Scrape KingOfSat transponders:
  --positions POS [POS ...]  satellite position(s), e.g. 28.2E 13.0E  (default: 28.2E)
  --kos-filter FILTER        Clear (FTA only), All, or Encrypted  (default: Clear)
  --kos-cl LANG              channel language filter  (default: eng)

Octopus connection (steps 2, 3, 4, 5):
  --octopus-host HOST        hostname or IP of the Octopus NET device (required)
  --octopus-user USER        (default: admin)
  --octopus-password PASS    overrides OCTOPUS_PASSWORD env var

Step 3 — Scan options:
  --scan-poll-interval SEC   (default: 1)
  --scan-timeout SEC         (default: 600)

Step 4 — Add channels to DMS:
  --add-channels SPEC        all | video | audio | comma-separated names |
                             name@frequency e.g. 'BBC One HD@10773,ITV HD@11386' |
                             path to a .txt file (one name per line, # comments supported)
                             Omit to use the interactive editor (TTY) or list available channels.

Channels DVR connection (steps 6, 8):
  --channels-dvr-host HOST   (default: localhost)
  --channels-dvr-port PORT   (default: 8089)
  --channels-dvr-sid SID     overrides CHANNELS_DVR_SID env var

Step 6 — Gracenote options:
  --station-list NAME        Gracenote station list  (default: GBR-1000193-DEFAULT)

Step 7 — M3U enrichment options:
  --mapping-region REGION         built-in region mappings to use  (default: uk)

Step 8 — Push to Channels DVR:
  --source-id ID                  Channels DVR M3U source ID for TV channels
  --source-display-name NAME      display name for the TV source
  --source-id-radio ID            Channels DVR M3U source ID for radio channels
  --source-display-name-radio NAME display name for the radio source
                                  (at least one of --source-id / --source-id-radio required for step 8)

State:
  --state-dir DIR            intermediate state files  (default: .pipeline_state)
```

### Channel name mappings (step 7)

The enrichment step matches Octopus channel callsigns against Gracenote automatically. For channels that don't match automatically, built-in regional mapping files provide Name→Callsign lookups:

```bash
python pipeline.py --steps 7 8 --source-id <your-source-id> --mapping-region uk
```

Mappings live in [m3u/resources/channel_name_to_callsign_mappings/](m3u/resources/channel_name_to_callsign_mappings/), organised by region. Each region has separate `tv.csv` and `radio.csv` files:

```csv
Name,Callsign
BBC One NW HD,BBC1NWHD
ITV1 HD,ITV1GHD
```

If you find a channel isn't matched, please add it to the appropriate regional CSV and open a PR.

---

## Module structure

```
sat_to_ip_helpers/
│
├── pipeline.py                      ← the main entry point; wires modules together
│
├── king_of_sat_scraper/             ← scrapes transponder data from kingofsat.net
│   ├── client.py                    ← KingOfSatClient (HTTP)
│   ├── scraper.py                   ← KingOfSatScraper (pure HTML → Transponder objects)
│   ├── transponder.py               ← Transponder dataclass
│   ├── channel.py                   ← Channel dataclass
│   └── tests/
│       └── test_client.py
│
├── octopus_api/                     ← talks to the Octopus NET REST API
│   ├── client.py                    ← OctopusClient (HTTP)
│   ├── transponders.py              ← pure functions: format Transponders → Octopus JSON
│   └── tests/
│       ├── test_client.py
│       └── test_transponders.py
│
├── channels_dvr/                    ← talks to the Channels DVR REST API
│   ├── client.py                    ← ChannelsDVRClient (HTTP)
│   └── tests/
│       └── test_client.py
│
├── m3u/                             ← M3U enrichment logic
│   ├── enrichment.py                ← pure functions: match channels → Gracenote IDs
│   ├── resources/                   ← Gracenote JSON files and regional mapping CSVs
│   └── tests/
│       └── test_enrichment.py
│
└── utils_pipeline/                  ← pipeline helpers (pure functions + exceptions)
    ├── csv_mappings.py              ← load regional Name→Callsign mappings
    ├── channels.py                  ← DMS channel filtering
    ├── dms.py                       ← interactive DMS editor
    ├── scan.py                      ← scan diff + DMS restore logic
    ├── exceptions.py                ← PipelineError hierarchy
    └── tests/
```

### Key design principles

**Pure functions are separate from HTTP clients.**
Each module splits into two concerns:
- A `client.py` that makes HTTP requests (hard to test without a server)
- Pure functions (`scraper.py`, `transponders.py`, `enrichment.py`) that transform data (trivial to unit test)

**Clients use injectable sessions for testability.**
Every HTTP client follows the same pattern:

```python
# Production: use the factory classmethod
client = OctopusClient.login("http://octopus.local", "admin", password)

# Tests: inject a mock session — no real HTTP needed
session = MagicMock()
client = OctopusClient("http://octopus.local", session)
client.start_scan(["28.2E"])
session.post.assert_called_once_with(...)
```

The three clients follow the same shape:

| Client | Factory classmethod |
|---|---|
| `KingOfSatClient(base_url, session)` | `.create(base_url)` |
| `OctopusClient(base_url, session)` | `.login(base_url, user, password)` |
| `ChannelsDVRClient(base_url, session)` | `.with_sid(host, port, sid)` |

**`pipeline.py` owns all I/O.**
It reads files, writes state, and creates clients. The modules themselves never touch the filesystem.

---

## Running tests

```bash
pytest
```

Tests live inside each module's `tests/` directory. The pure-function tests need no mocking; the client tests use `unittest.mock.MagicMock` to stub HTTP responses.

```bash
# Run just one module's tests
pytest king_of_sat_scraper/tests/
pytest m3u/tests/ -v
```

---

## Adding a new satellite position

```bash
python pipeline.py --positions 28.2E 13.0E --octopus-host octopus.local \
  --source-id <your-tv-source-id> --source-id-radio <your-radio-source-id>
```

The pipeline will scrape both positions, merge them into a single transponder list, upload it, scan, and add the found channels to the DMS.
