import argparse
import csv
import json
import re


def normalize_name(name):
    """Normalize a channel name for comparison."""
    if not name:
        return ""
    # Lowercase, remove non-alphanumeric characters, and strip spaces
    return re.sub(r'[^a-z0-9]', '', name.lower())


def load_name_to_callsign_csv(csv_file):
    """Load explicit channel name -> callsign mappings from CSV."""
    mapping = {}
    if not csv_file:
        return mapping
    
    with open(csv_file, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
            name_idx = headers.index("Name")
            callsign_idx = headers.index("Callsign")
        except (ValueError, StopIteration):
            print(f"⚠️  Invalid CSV headers in {csv_file}. Expected 'Name,Callsign'.")
            return mapping

        for row in reader:
            if len(row) > max(name_idx, callsign_idx):
                name = row[name_idx].strip()
                callsign = row[callsign_idx].strip()
                mapping[name] = callsign
    print(f"✅ Loaded {len(mapping)} explicit mappings from {csv_file}")
    return mapping


def load_gracenote_data(json_file):
    """Load Gracenote JSON and build lookup maps."""
    callsign_to_info = {}
    name_to_callsign = {}
    
    with open(json_file, encoding="utf-8") as f:
        data = json.load(f)
        # Handle potential nested list from Channels DVR export
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
            data = data[0]
            
        for entry in data:
            callsign = entry.get("callSign", "").strip()
            name = entry.get("name", "").strip()
            station_id = entry.get("stationId", "").strip()
            channel_num = entry.get("channel", "").strip()
            
            if callsign:
                callsign_to_info[callsign] = {
                    "channel": channel_num,
                    "stationId": station_id,
                    "name": name,
                }
                # Index both the name and the callsign for fallback matching
                if name:
                    name_to_callsign[normalize_name(name)] = callsign
                if callsign:
                    name_to_callsign[normalize_name(callsign)] = callsign
                    
    print(f"✅ Loaded {len(callsign_to_info)} Gracenote entries from {json_file}")
    return callsign_to_info, name_to_callsign


def enrich_m3u(input_m3u, output_m3u, explicit_mappings, gracenote_info, gracenote_names):
    """Process M3U and inject Gracenote metadata."""
    enriched = 0
    skipped = 0

    with open(input_m3u, encoding="utf-8") as infile, open(output_m3u, "w", encoding="utf-8") as outfile:
        outfile.write("#EXTM3U\n")
        lines = infile.readlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("#EXTINF:"):
                channel_name = line.split(",", 1)[1].strip()
                stream_url = lines[i + 1].strip() if i + 1 < len(lines) else ""

                # 1. Try explicit CSV mapping
                callsign = explicit_mappings.get(channel_name)
                
                # 2. Try automatic name matching
                if not callsign:
                    norm_name = normalize_name(channel_name)
                    callsign = gracenote_names.get(norm_name)

                if not callsign or callsign not in gracenote_info:
                    print(f"⚠️  No match found for: {channel_name} — Skipping")
                    skipped += 1
                    i += 2
                    continue

                info = gracenote_info[callsign]
                
                # Build enriched tag
                # We include channel-id (callsign), tvg-name, and tvc-guide-stationid
                enriched_line = (
                    f'#EXTINF:-1 channel-id="{callsign}" '
                    f'tvc-guide-stationid="{info["stationId"]}" '
                    f'tvg-name="{channel_name}",{channel_name}'
                )
                
                outfile.write(enriched_line + "\n")
                outfile.write(stream_url + "\n")
                enriched += 1
                i += 2
            else:
                i += 1

    print("\n📊 Summary:")
    print(f"   ✅ Enriched: {enriched}")
    print(f"   ⚠️  Skipped: {skipped}")
    print(f"\n📝 Output written to: {output_m3u}")


def main():
    parser = argparse.ArgumentParser(
        description="Enrich M3U with Channels DVR metadata using Gracenote JSON.")
    parser.add_argument("input_m3u", help="Input M3U file path")
    parser.add_argument("gracenote_json", help="Gracenote JSON file from Channels DVR")
    parser.add_argument("output_m3u", help="Path for the enriched M3U output")
    parser.add_argument("--mapping-csv", help="Optional CSV mapping 'Name,Callsign'")

    args = parser.parse_args()

    explicit_mappings = load_name_to_callsign_csv(args.mapping_csv)
    gracenote_info, gracenote_names = load_gracenote_data(args.gracenote_json)
    
    enrich_m3u(
        args.input_m3u, 
        args.output_m3u, 
        explicit_mappings, 
        gracenote_info, 
        gracenote_names
    )


if __name__ == "__main__":
    main()
