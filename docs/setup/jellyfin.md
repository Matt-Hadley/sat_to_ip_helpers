# Jellyfin setup (once)

Each run writes three files into `output_dir`, atomically:

| File        | Contents                                                                 |
|-------------|--------------------------------------------------------------------------|
| `tv.m3u`    | TV channels that matched the guide, in receiver order                    |
| `radio.m3u` | Radio channels that matched (a header-only file when there are none)     |
| `guide.xml` | The XMLTV feed filtered to those channels (only when a guide is set)     |

Each entry has `tvg-id` (the XMLTV channel id), `tvg-name`, `tvg-chno` (receiver position) and `group-title` (`TV` / `Radio`). Jellyfin's M3U tuner matches `tvg-id` to the XMLTV `<channel id>`, so no manual guide mapping is needed for matched channels.

Then the run starts Jellyfin's **Refresh Guide** scheduled task so the changes show up straight away.

## 1. Give the Jellyfin container the files

Mount `output_dir` into the container, read-only is enough, e.g. with Docker Compose:

```yaml
services:
  jellyfin:
    volumes:
      - /srv/jellyfin/livetv:/livetv:ro
```

The paths below (`/livetv/...`) are the container-side paths.

## 2. Add the tuner(s) and the guide

In the Jellyfin dashboard, under **Live TV** (menu names vary between versions, check yours):

1. **Tuner Devices → +**, tuner type **M3U Tuner**, file `/livetv/tv.m3u`. Save.
2. Optionally add a second M3U Tuner with `/livetv/radio.m3u`. Jellyfin's M3U tuner has no radio flag, so these show up as TV channels in the `Radio` group.
3. **TV Guide Data Providers → +**, **XMLTV**, file `/livetv/guide.xml`, used for the tuner(s) above.

## 3. Create an API key

Dashboard → **API Keys → +** (under *Advanced* in recent versions; check yours), name it e.g. `sat-to-ip`. Every Jellyfin API key has administrator rights, which starting a scheduled task needs. Put it in an environment variable on the host that runs `sat-to-ip`, e.g. `JELLYFIN_API_KEY`.

## 4. Configure the publisher

```toml
[guides.freeview]
type = "xmltv"
url = "https://example.org/epg.xml.gz"
mappings = "uk"

[[publishers]]
type = "jellyfin"
url = "http://localhost:8096"          # default
api_key_env = "JELLYFIN_API_KEY"       # required unless refresh = false
output_dir = "/srv/jellyfin/livetv"    # host path mounted into the container
guide = "freeview"                     # optional; without it every channel is written, with no guide.xml
# refresh = false                      # only write files; Jellyfin's daily Refresh Guide picks them up
```

Check it with `sat-to-ip check-config -c sat-to-ip.toml`, then `sat-to-ip run -c sat-to-ip.toml --dry-run` (writes to a temp directory and calls nothing).

## Known quirks

- Jellyfin caches an XMLTV file for an hour, so a refresh within an hour of the previous one can still show the old guide; the next refresh picks it up.
- Jellyfin reads a channel name that starts with a number and a space (e.g. `5 USA`) as "number + name". The pipeline therefore writes Jellyfin labels as `<channel number> <name>` (e.g. `12 5 USA`), which Jellyfin splits back into number 12 and name "5 USA".
- The authentication header used is `Authorization: MediaBrowser Token="…"`; the old `X-Emby-Token` header only works when legacy authorization is turned on in Jellyfin.
