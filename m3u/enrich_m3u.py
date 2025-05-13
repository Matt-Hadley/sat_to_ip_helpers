"""
Enrich an M3U playlist using channel metadata from CSV and Gracenote JSON.

References:
- Channels DVR Custom Channels Documentation:
  https://getchannels.com/docs/channels-dvr-server/how-to/custom-channels/

Input:
- M3U file: Original playlist
- CSV file: Maps channel name -> callsign
- JSON file: Maps callsign -> Gracenote channel number and metadata

Output:
- Enriched M3U compatible with Channels DVR
"""

import argparse
import csv
import json


def load_name_to_callsign(csv_file):
    mapping = {}
    with open(csv_file, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader)
        name_idx = headers.index("Name")
        callsign_idx = headers.index("Callsign")

        for row in reader:
            if len(row) > max(name_idx, callsign_idx):
                name = row[name_idx].strip()
                callsign = row[callsign_idx].strip()
                mapping[name] = callsign
    print(f"✅ Loaded {len(mapping)} name→callsign mappings from {csv_file}")
    return mapping


def load_callsign_to_channel_info(json_file):
    mapping = {}
    with open(json_file, encoding="utf-8") as f:
        data = json.load(f)
        for entry in data:
            callsign = entry.get("callSign", "").strip()
            if callsign:
                mapping[callsign] = {
                    "channel": entry.get("channel", "").strip(),
                    "stationId": entry.get("stationId", "").strip(),
                    "name": entry.get("name", "").strip(),
                }
    print(
        f"✅ Loaded {len(mapping)} Gracenote channel entries from {json_file}")
    return mapping


def enrich_m3u(input_m3u, output_m3u, name_to_callsign, callsign_to_info):
    enriched = 0
    missing_name = 0
    missing_callsign = 0

    with open(input_m3u, encoding="utf-8") as infile, open(output_m3u, "w", encoding="utf-8") as outfile:
        outfile.write("#EXTM3U\n")
        lines = infile.readlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("#EXTINF:"):
                channel_name = line.split(",", 1)[1].strip()
                stream_url = lines[i + 1].strip()

                callsign = name_to_callsign.get(channel_name)
                if not callsign:
                    print(
                        f"⚠️  No callsign found for: {channel_name} — Skipping")
                    missing_name += 1
                    i += 2
                    continue  # Skip this entry entirely

                info = callsign_to_info.get(callsign)
                if not info:
                    print(
                        f"⚠️  Callsign not found in JSON: {callsign} (from {channel_name}) — Skipping")
                    missing_callsign += 1
                    i += 2
                    continue  # Skip this entry entirely

                enriched_line = (
                    f'#EXTINF:-1 channel-id="{callsign}" '
                    f'tvg-name="{channel_name}",{callsign}'
                )
                print(
                    f"✅ Enriched: {channel_name} → ID: {callsign}, Channel: {info['channel']}")
                outfile.write(enriched_line + "\n")
                outfile.write(stream_url + "\n")
                enriched += 1
                i += 2
            else:
                i += 1

    # Summary
    print("\n📊 Summary:")
    print(f"   ✅ Enriched: {enriched}")
    print(f"   ⚠️  Missing name mappings: {missing_name}")
    print(f"   ⚠️  Missing callsign in JSON: {missing_callsign}")
    print(f"\n📝 Output written to: {output_m3u}")


def main():
    parser = argparse.ArgumentParser(
        description="Enrich M3U with Channels DVR metadata using Gracenote JSON.")
    parser.add_argument("input_m3u", help="Input M3U file")
    parser.add_argument(
        "mapping_csv", help="CSV file: Channel name -> Callsign")
    parser.add_argument("gracenote_json", help="Gracenote JSON file")
    parser.add_argument("output_m3u", help="Output enriched M3U file")

    args = parser.parse_args()

    name_to_callsign = load_name_to_callsign(args.mapping_csv)
    callsign_to_info = load_callsign_to_channel_info(args.gracenote_json)
    enrich_m3u(args.input_m3u, args.output_m3u,
               name_to_callsign, callsign_to_info)


if __name__ == "__main__":
    main()
