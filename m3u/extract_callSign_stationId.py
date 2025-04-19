import csv
import json

# Input and output file paths
json_file = "uk_channel_gracenote.json"
csv_file = "callSign_to_stationId.csv"

# Read JSON data
with open(json_file, "r") as f:
    data = json.load(f)

# Handle nested array if it's a list of lists
if isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
    data = data[0]

# Open CSV file and write rows
with open(csv_file, "w", newline="") as f:
    writer = csv.writer(f)

    for item in data:
        call_sign = item.get("callSign", "")
        station_id = item.get("stationId", "")
        writer.writerow([call_sign, station_id])
