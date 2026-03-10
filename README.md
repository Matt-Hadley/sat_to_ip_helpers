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
  --position "28.2E" "13.0E" \
  --filter "Clear" \
  --cl "eng" \
  --output-dir "output"
```

**Parameters:**
- `--position`: The orbital position(s) of the satellite (e.g., `28.2E`, `13.0E`). Accepts multiple space-separated values to bundle into a single config. Default: `28.2E`
- `--filter`: Filter for channels (e.g., `Clear` for Free-to-Air, `All`, `Encrypted`). Default: `Clear`
- `--cl`: Language filter (e.g., `eng`, `fra`). Default: `eng`
- `--output-dir`: Directory where the resulting JSON tuning file will be saved. Default: `output`

**Next Steps (Octopus NET):**
1. Once you have generated the JSON transponder list, navigate to the Octopus NET web interface.
2. Go to the **Channel Search** page.
3. Under **Custom transponderlist**, upload the generated JSON file and save.
4. Select your newly configured **target** at the top of the page.
5. Click **Start Scan** to find channels matching your criteria.

### 2. M3U Enrichment tools (`m3u`)

A set of tools to enrich an M3U playlist (e.g., exported from your Sat>IP server) with **Gracenote Station IDs** so that it integrates seamlessly with **Channels DVR**. This allows Channels DVR to map standard EPG data to your satellite streams accurately.

#### Usage

**Step 1. Export M3U from Octopus NET**
To get the M3U playlist from your Octopus NET device:
1. Navigate to the Octopus NET web interface and go to the **Unicast (DMS)** page.
2. Ensure you have channels added (they should appear in the **DMS announced channels** panel).
3. Click the **M3U EXPORT** button to download your playlist.

**Step 2. Enrich the M3U Playlist**
To run the enrichment, you will need:
- Your original M3U playlist file from Step 1 (e.g. `~/Downloads/octopus_dms_export.m3u`)
- The raw Gracenote JSON dataset from Channels DVR (Download it from `http://127.0.0.1:8089/dvr/guide/stations/GBR-1000193-DEFAULT` and save it to e.g. `~/Downloads/my_gracenote.json`)

1. Run the enrichment script from the root of this project:
   ```bash
   # Simplest usage: Automatic name matching
   python m3u/scripts/enrich_m3u.py ~/Downloads/octopus_dms_export.m3u ~/Downloads/my_gracenote.json ~/Downloads/octopus_enriched.m3u
   
   # Advanced usage: Provide a manual CSV mapping for specific names
   python m3u/scripts/enrich_m3u.py ~/Downloads/octopus_dms_export.m3u ~/Downloads/my_gracenote.json ~/Downloads/octopus_enriched.m3u --mapping-csv ~/Downloads/channel_name_mappings.csv
   ```
   
   > [!TIP]
   > The script first tries to find matches in your optional CSV. If no CSV is provided or a name isn't found, it automatically attempts to match the M3U channel name against the Gracenote guide names using a "fuzzy" normalized comparison (ignoring case, spaces, and special characters).

2. The resulting `octopus_enriched.m3u` file can now be imported into Channels DVR as a Custom Channel source! The script will have injected `channel-id`, `tvg-name`, and most importantly `tvc-guide-stationid` tags on every line so that Channels DVR seamlessly matches your streams to its EPG interface.
