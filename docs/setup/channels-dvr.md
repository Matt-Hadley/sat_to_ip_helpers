# Channels DVR

**What you'll end up with:** one Channels DVR source for your satellite TV channels (and optionally a second one for radio). Every run refreshes the channels, with guide data from Gracenote, the same guide Channels DVR uses everywhere else.

## 1. Create the source in Channels DVR

Do this once in the Channels DVR web settings (`http://<your-server>:8089`). Menu names can differ slightly between versions.

1. Go to **Settings → Sources → Add Source → Custom Channels**.
2. Give it a name, e.g. `SatTV`, choose **MPEG-TS**, and paste anything as the playlist text for now (sat-to-ip replaces it on the first run).
3. If you want radio as well, repeat with a second source, e.g. `SatRadio`.

## 2. Add it to your config

```toml
[guides.gracenote]
type = "gracenote"
url = "http://localhost:8089"     # where Channels DVR runs
mappings = "uk"

[[publishers]]
type = "channels_dvr"
url = "http://localhost:8089"
guide = "gracenote"
tv_source = "SatTV"               # the source you created
# radio_source = "SatRadio"       # if you made one for radio
```

`tv_source` and `radio_source` are the source's ID, which is the name you gave it in step 1. If a run says the source doesn't exist, check the spelling (and capitals) match.

## 3. Try it

```bash
sat-to-ip run -c sat-to-ip.toml --dry-run
sat-to-ip run -c sat-to-ip.toml
```

Then look at **Guide** in Channels: your satellite channels appear with their listings.

## Good to know

- **Your source settings are left alone.** A run only swaps in the new channel list, keeping everything else you set for the source in Channels (stream limit, starting number, logos and so on), then asks Channels to reload it straight away instead of waiting for its next scheduled refresh.
- **Channel numbers:** by default Channels numbers the channels itself, counting up from the source's starting number (e.g. 9000). To use the numbers from your channel list instead, add `channel_numbers = "list"` to the publisher. The run then tells Channels to prefer those numbers. To choose the numbers, see [Choose your channels](../../README.md#5-choose-your-channels) in the README.
- **No login needed** on a home network. If your Channels DVR does ask for one, copy the `SID` cookie from your browser while signed in. Put it in an environment variable, and add `sid_env = "CHANNELS_DVR_SID"` to both the guide and the publisher.
- **Channels without a guide listing are left out**, and the run lists them. To match one by hand, see "A channel has no TV guide" in the [README](../../README.md#troubleshooting).
