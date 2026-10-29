# Plex through Threadfin setup (once)

Plex only talks to HDHomeRun-style tuners, so [Threadfin](https://github.com/Threadfin/Threadfin) sits in between: it reads the playlist and guide files this pipeline writes and presents them to Plex as an HDHomeRun. Live TV in Plex needs **Plex Pass**.

Each run writes, atomically, into `output_dir`: `tv.m3u`, `radio.m3u` (header-only when empty) and `guide.xml` (the XMLTV feed filtered to the matched channels; only when a guide is set). Entries carry `tvg-id` (the XMLTV channel id), `tvg-name`, `tvg-chno` and `group-title` (`TV` / `Radio`).

## 1. Give the Threadfin container the files

Mount `output_dir` into the container, e.g. with Docker Compose:

```yaml
services:
  threadfin:
    image: fyb3roptik/threadfin
    ports:
      - "34400:34400"             # Threadfin's default port
    volumes:
      - /srv/threadfin/files:/livetv:ro
```

The paths below (`/livetv/...`) are the container-side paths.

## 2. Playlist, XMLTV and mapping in Threadfin

In Threadfin's web UI (`http://<host>:34400/web/`):

1. **Playlist → New → M3U**: "File path or URL of the M3U" = `/livetv/tv.m3u`. Set "Tuner / Streams" to the number of streams your receiver can serve at once. Add `/livetv/radio.m3u` as a second playlist only if you want radio in Plex.
2. **XMLTV → New**: file `/livetv/guide.xml`.
3. **Mapping**: when Threadfin first sees a channel whose `tvg-id` matches a `<channel id>` in an XMLTV file, it maps and activates it on its own. Check the list once; channels without a match stay inactive. The channel number comes from `tvg-chno`.
4. **Settings**: "Schedule for updating (Playlist, XMLTV, Backup)" decides when Threadfin re-reads the files by itself (e.g. `0400`).

### Optional: update straight after each run

Threadfin has an API (`POST /api/`, commands `update.xmltv`, `update.m3u`, …). To use it:

- **Settings → API Interface**: on.
- If you also turn on **API Authentication** (in the authentication settings; check where in your version), create a Threadfin user with **API Access** and give the publisher its `username` and `password_env`.

The publisher then sends `update.xmltv` (when a guide is written) and `update.m3u`, which rebuilds Threadfin's channel list and maps new channels.

## 3. Add Threadfin to Plex

Plex Web → **Settings → Live TV & DVR → Set up Plex DVR** (check the wording in your version). If Threadfin isn't found automatically, enter its address by hand, `<host>:34400`. For the guide, choose XMLTV and give Threadfin's guide URL, `http://<host>:34400/xmltv/threadfin.xml`, then confirm the channel list.

## 4. Configure the publisher

```toml
[guides.freeview]
type = "xmltv"
url = "https://example.org/epg.xml.gz"
mappings = "uk"

[[publishers]]
type = "threadfin"
output_dir = "/srv/threadfin/files"     # host path mounted into the container
guide = "freeview"                      # optional; without it every channel is written, with no guide.xml
# api_url = "http://localhost:34400"    # optional: update Threadfin right after writing
# username = "sat-to-ip"                # only with Threadfin's API Authentication on
# password_env = "THREADFIN_PASSWORD"
```

Check it with `sat-to-ip check-config -c sat-to-ip.toml`, then `sat-to-ip run -c sat-to-ip.toml --dry-run` (writes to a temp directory and calls nothing).
