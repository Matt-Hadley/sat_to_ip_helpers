# Plex (through Threadfin)

**What you'll end up with:** your satellite channels in Plex's **Live TV & DVR**, with a 7-day TV guide. Live TV in Plex needs **Plex Pass**.

Plex can only use TV tuner boxes (HDHomeRun-style), not channel lists. [Threadfin](https://github.com/Threadfin/Threadfin) is a free app that bridges the gap: it reads the files sat-to-ip writes and shows itself to Plex as a tuner.

```mermaid
flowchart LR
    TOOL["sat-to-ip"] -- "writes channels + guide" --> FILES[("folder")]
    FILES --> TF["Threadfin"]
    TF -- "looks like a tuner" --> PLEX["Plex"]
```

## 1. Run Threadfin and let it see the files

Pick a folder on the computer that runs sat-to-ip, e.g. `/srv/threadfin/livetv`, and mount it into Threadfin. With Docker Compose:

```yaml
services:
  threadfin:
    image: fyb3roptik/threadfin
    ports:
      - "34400:34400"
    volumes:
      - /srv/threadfin/livetv:/livetv:ro
```

Inside the container the files are then at `/livetv/tv.m3u`, `/livetv/radio.m3u` and `/livetv/guide.xml`.

## 2. Set up Threadfin

Open Threadfin at `http://<your-server>:34400/web/`.

1. **Playlist → New → M3U:** enter `/livetv/tv.m3u`. Set **Tuner / Streams** to how many channels your receiver can stream at once. Add `/livetv/radio.m3u` as a second playlist only if you want radio in Plex.
2. **XMLTV → New:** enter `/livetv/guide.xml`.
3. **Settings → EPG Source:** choose **XEPG**. With the default (PMS) the mapping page stays empty.
4. **Filter → New → M3U:** pick the playlist and save. Channels only appear for mapping once a filter lets them through.
5. **Mapping:** channels that match the guide switch themselves on. Check the list once, since anything without a match stays off.
6. **Settings → Schedule for updating:** choose when Threadfin re-reads the files, e.g. `0500`, an hour after your weekly run.

## 3. Add Threadfin to Plex

In Plex, go to **Settings → Live TV & DVR → Set up Plex DVR**. Wording varies by version.

1. If Threadfin isn't found automatically, enter its address: `<your-server>:34400`.
2. For the guide, choose **XMLTV** and enter `http://<your-server>:34400/xmltv/threadfin.xml`.
3. Confirm the channel list.

## 4. Add it to your config

```toml
[guides.freeview]
type = "xmltv"
url = "https://raw.githubusercontent.com/dp247/Freeview-EPG/master/epg.xml"
mappings = "freeview-epg"

[[publishers]]
type = "threadfin"
output_dir = "/srv/threadfin/livetv"    # the folder from step 1 (its path on this computer)
guide = "freeview"
```

If you also use Jellyfin, give it a **different** folder.

## 5. Try it

```bash
sat-to-ip run -c sat-to-ip.toml --dry-run
sat-to-ip run -c sat-to-ip.toml
```

## Optional: update Threadfin straight after each run

Instead of waiting for Threadfin's schedule, sat-to-ip can tell it to reload:

1. In Threadfin, turn on **Settings → API Interface**.
2. Add `api_url = "http://localhost:34400"` to the publisher.
3. If you also turn on Threadfin's **API Authentication**, create a Threadfin user with API access and add:

   ```toml
   username = "sat-to-ip"
   password_env = "THREADFIN_PASSWORD"
   ```

Note that this makes Threadfin reload *all* its playlists, not just these.

## If channels show up but won't play

Octopus NET sends its streams over RTSP with the picture on UDP, and it doesn't offer TCP or HTTP instead. Threadfin needs to receive that UDP:

- **Linux:** add `network_mode: host` to the Threadfin service (and drop `ports:`), so the UDP reaches the container.
- **Docker Desktop (Mac or Windows):** UDP from the box doesn't reach the container. Use Channels DVR as the source instead (below), or run Threadfin on a Linux machine.

### Feeding Threadfin from Channels DVR

If you already publish to [Channels DVR](channels-dvr.md), Threadfin can use its channel list and guide instead of sat-to-ip's files. Channels DVR handles the UDP, and you don't need the `threadfin` publisher or the folder from step 1:

- **Playlist:** `http://<channels-dvr>:8089/devices/ANY/channels.m3u?format=ts`
- **XMLTV:** `http://<channels-dvr>:8089/devices/ANY/guide/xmltv?duration=604800`

Set Threadfin's **Settings → Buffer** to **FFmpeg**. Plex can't play Channels DVR's raw streams directly, and the FFmpeg buffer makes them playable.
