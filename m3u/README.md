# Download Gracenote channel IDs (Optional)
1. Download the UK channels from Gracenote via Channels DVR:
    http://127.0.0.1:8089/dvr/guide/stations/GBR-1000193-DEFAULT

2. Save the JSON into a file `uk_channel_gracenote.json`.

3. Run the `extract_callSign_stationId.py` script, to generate callSign to stationId mappings.

# Enrich the Octopus M3U
1. Download the Octopus M3U as `octopus_original.m3u`.

2. Update `channel_name_mappings.csv` as required.

3. Run `enrich_m3u.py` using `python enrich_m3u.py octopus_original.m3u channel_name_mappings.csv _enriched.m3u`.
