"""
Enrich an M3U playlist with metadata for Channels DVR custom channels.

References:
- Channels DVR Custom Channels Documentation:
  https://getchannels.com/docs/channels-dvr-server/how-to/custom-channels/

Input:
- M3U file: Original playlist
- CSV file: Channel mappings in format:
  Display Name,Channel-ID,Channel Number,Group Title (group is ignored)

Output:
- Enriched M3U file compatible with Channels DVR
"""

import argparse
import csv


def load_mappings(csv_file):
    mappings = {}
    with open(csv_file, newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 3:
                name, channel_id, chno = row[:3]
                mappings[name.strip()] = {
                    "channel-id": channel_id.strip(),
                    "channel-number": chno.strip()
                }
    return mappings


def enrich_m3u(input_m3u, output_m3u, mappings):
    with open(input_m3u, encoding='utf-8') as infile, open(output_m3u, 'w', encoding='utf-8') as outfile:
        outfile.write("#EXTM3U\n")
        lines = infile.readlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("#EXTINF:"):
                channel_name = line.split(",", 1)[1].strip()
                stream_url = lines[i + 1].strip()

                if channel_name in mappings:
                    meta = mappings[channel_name]
                    enriched_line = (
                        f'#EXTINF:-1 channel-id="{meta["channel-id"]}"'
                        f' channel-number="{meta["channel-number"]}"'
                        f',{meta["channel-id"]}'
                    )
                else:
                    enriched_line = f"#EXTINF:-1,{channel_name}"

                outfile.write(enriched_line + "\n")
                outfile.write(stream_url + "\n")
                i += 2
            else:
                i += 1


def main():
    parser = argparse.ArgumentParser(
        description="Enrich M3U with Channels DVR metadata.")
    parser.add_argument("input_m3u", help="Input M3U file")
    parser.add_argument("mappings_csv", help="CSV file with channel mappings")
    parser.add_argument("output_m3u", help="Output enriched M3U file")

    args = parser.parse_args()

    mappings = load_mappings(args.mappings_csv)
    enrich_m3u(args.input_m3u, args.output_m3u, mappings)
    print(f"✅ Enriched M3U saved as: {args.output_m3u}")


if __name__ == "__main__":
    main()
