# Example Gracenote station lists

Snapshots of what step 6 downloads from Channels DVR (`/dvr/guide/stations/GBR-1000193-DEFAULT`), kept to show the format. The pipeline does not read them.

Each entry looks like:

```json
{
  "type": "",
  "name": "",
  "channel": "1",
  "callSign": "123TVGR",
  "stationId": "53409",
  "preferredImage": {
    "uri": "https://tmsimg.fancybits.co/assets/s53409_ll_h15_ac.png?w=360&h=270",
    "height": "270",
    "width": "360",
    "primary": "true",
    "category": "Logo",
    "text": "",
    "tier": ""
  },
  "videoQuality": {
    "signalType": "Digital",
    "videoType": "SDTV",
    "truResolution": ""
  }
}
```

The pipeline uses `callSign`, `name`, `stationId` and `channel`.
