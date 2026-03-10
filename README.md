# sat_to_ip_helpers

A collection of Python helper scripts for setting up and managing a Sat > IP (Satellite to IP) environment. 
This repository contains tools to assist with hardware tuning configurations and IPTV playlist enrichment, specifically targeting combinations like Digital Devices Octopus NET and Channels DVR.

## Prerequisites

- **Python**: 3.10 or higher
- **Dependencies**: Install the required Python packages:
  ```bash
  pip install -r requirements.txt
  ```

## Components

The repository is divided into two main toolsets:

### 1. KingOfSat Scraper (`king_of_sat_scraper`)

A tool to scrape satellite transponder data from [KingOfSat](https://en.kingofsat.net/) and generate JSON tuning configuration files compatible with the **Digital Devices Octopus NET** Sat>IP server.

#### Usage

Run the generation script to scrape transponder data for a specific satellite position:

```bash
python -m king_of_sat_scraper.scripts.generate_transponders_list_for_digital_devices_octopus \
  --position "28.2E" \
  --filter "Clear" \
  --cl "eng" \
  --output-dir "output"
```

**Parameters:**
- `--position`: The orbital position of the satellite (e.g., `28.2E`, `13.0E`). Default: `28.2E`
- `--filter`: Filter for channels (e.g., `Clear` for Free-to-Air, `All`, `Encrypted`). Default: `Clear`
- `--cl`: Language filter (e.g., `eng`, `fra`). Default: `eng`
- `--output-dir`: Directory where the resulting JSON tuning file will be saved. Default: `output`

### 2. M3U Enrichment tools (`m3u`)

A set of tools to enrich an M3U playlist (e.g., exported from your Sat>IP server) with **Gracenote Station IDs** so that it integrates seamlessly with **Channels DVR**. This allows Channels DVR to map standard EPG data to your satellite streams accurately.

#### Usage

**Step 1. (Optional) Extract Gracenote Station IDs**
If you need to generate a mapping of CallSigns to Station IDs from Channels DVR's Gracenote data:
1. Download the channel guide data from your local Channels DVR instance:
   `http://127.0.0.1:8089/dvr/guide/stations/GBR-1000193-DEFAULT` (Replace with your specific lineup ID).
2. Save this JSON file as `uk_channel_gracenote.json` in the `m3u` directory.
3. Run the extraction script:
   ```bash
   cd m3u
   python extract_callSign_stationId.py
   ```
   This will output a lightweight `callSign_to_stationId.csv`.

**Step 2. Enrich the M3U Playlist**
You will need your original M3U playlist, a CSV mapping your channel names to their Gracenote CallSigns (e.g., a hand-crafted `channel_name_mappings.csv`), and the Gracenote JSON from Channels DVR.
1. Run the enrichment script:
   ```bash
   python m3u/enrich_m3u.py m3u/octopus_original.m3u m3u/channel_name_mappings.csv m3u/uk_channel_gracenote.json m3u/octopus_enriched.m3u
   ```
2. The output file (`octopus_enriched.m3u`) can imported into Channels DVR as a Custom Channel source, complete with `channel-id` and `tvg-name` tags for EPG matching.
