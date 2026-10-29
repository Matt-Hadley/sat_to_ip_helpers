# Jellyfin

**What you'll end up with:** your satellite channels under Jellyfin's **Live TV**, with a 7-day TV guide. Every run writes three files for Jellyfin to read, then asks Jellyfin to reload them straight away:

- `tv.m3u`: your TV channels
- `radio.m3u`: your radio channels
- `guide.xml`: the TV guide for those channels

## 1. Let Jellyfin see the files

Pick a folder on the computer that runs sat-to-ip, e.g. `/srv/jellyfin/livetv`. If Jellyfin runs in Docker, mount that folder into the container; with Docker Compose:

```yaml
services:
  jellyfin:
    volumes:
      - /srv/jellyfin/livetv:/livetv:ro
```

Inside the container the files are then at `/livetv/tv.m3u` and so on. If Jellyfin isn't in Docker, use the folder's real path in the next step.

## 2. Add the channels and the guide in Jellyfin

In the Jellyfin dashboard, go to **Live TV**. Menu names can differ between versions.

1. **Tuner Devices → +**, type **M3U Tuner**, file `/livetv/tv.m3u`. Save.
2. *(Optional)* Add a second **M3U Tuner** with `/livetv/radio.m3u`. Jellyfin has no separate radio section, so these appear as channels in a "Radio" group.
3. **TV Guide Data Providers → +**, choose **XMLTV**, file `/livetv/guide.xml`, and tick the tuner(s) above.

You don't need to map channels to the guide by hand: each channel already carries its guide ID.

## 3. Create an API key

So sat-to-ip can tell Jellyfin to reload:

1. Go to **Dashboard → API Keys → +** (under *Advanced* in recent versions) and name it `sat-to-ip`.
2. Put the key in an environment variable, e.g. `export JELLYFIN_API_KEY='…'`.

## 4. Add it to your config

```toml
[guides.freeview]
type = "xmltv"
url = "https://raw.githubusercontent.com/dp247/Freeview-EPG/master/epg.xml"
mappings = "freeview-epg"

[[publishers]]
type = "jellyfin"
url = "http://localhost:8096"          # where Jellyfin runs
api_key_env = "JELLYFIN_API_KEY"
output_dir = "/srv/jellyfin/livetv"    # the folder from step 1 (its path on this computer)
guide = "freeview"
# refresh = false                      # don't ask Jellyfin to reload; it picks the files up on its daily refresh
```

If you also use Threadfin, give it a **different** folder.

## 5. Try it

```bash
sat-to-ip run -c sat-to-ip.toml --dry-run    # writes to a temporary folder, calls nothing
sat-to-ip run -c sat-to-ip.toml
```

## Good to know

- **The guide can lag by up to an hour:** Jellyfin keeps a guide file for an hour, so a second run inside an hour may still show the old guide until its next refresh.
- **Channel names starting with a number** (e.g. `5 USA`): Jellyfin would read the `5` as a channel number. sat-to-ip prevents that by writing the real channel number in front, so it still shows as "5 USA".
- **Regional channels:** ITV1 and Channel 4 come in regional versions in the guide. Add your region in a `mappings_file`, as explained under "A channel has no TV guide" in the [README](../../README.md#troubleshooting).
