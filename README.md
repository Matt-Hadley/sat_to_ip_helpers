# sat-to-ip

Watch free satellite TV and radio in **Channels DVR**, **Jellyfin** or **Plex**, with a full TV guide, and keep the channel list up to date automatically.

It's for anyone with a satellite dish and a **SAT>IP receiver**: a box that turns the dish signal into streams on your home network. Out of the box it's set up for the UK free-to-air channels on Astra 28.2°E, the satellite Freesat uses.

**Supported receivers:** Digital Devices Octopus NET. Each receiver is a small plug-in, so support for other boxes can be added without touching the rest. See [CONTRIBUTING.md](CONTRIBUTING.md#a-receiver-from-another-manufacturer), or open an issue saying which box you have.

```mermaid
flowchart LR
    DISH(("🛰️ Satellite dish")) --> OCT["SAT>IP receiver<br/>e.g. Octopus NET"]
    OCT --> TOOL["<b>sat-to-ip</b><br/>picks your channels<br/>and adds the TV guide"]
    GUIDE["📅 TV guide data<br/>(free, from the internet)"] --> TOOL
    TOOL --> CDVR["Channels DVR"]
    TOOL --> JF["Jellyfin"]
    TOOL --> TF["Threadfin"] --> PLEX["Plex"]
    CDVR & JF & PLEX --> TV["📺 TV, phone, tablet"]
```

## What it does for you

Without it, getting satellite channels into a DVR app means doing a handful of fiddly steps by hand every time channels move: telling the receiver what to scan, picking channels, downloading a playlist, matching every channel to its guide listing, and loading it into each app.

`sat-to-ip` does all of that in one go:

1. **Rescans the satellite.** It fetches the current list of frequencies and has your receiver scan them, so moved channels are found again.
2. **Applies your channel list.** You keep a simple text file of the channels you want, in the order you want them.
3. **Adds the TV guide.** It matches each channel to its guide listing.
4. **Updates every app you use,** all from the same channel list.

Run it once, or let it run by itself once a week. If something looks wrong, for example the dish was covered in snow and the scan found half the usual channels, it stops and changes nothing (see [Keeping your channels safe](#keeping-your-channels-safe)).

## What you need

- A dish pointed at Astra 28.2°E and a supported SAT>IP receiver (today: the **Octopus NET**), with its admin password.
- A computer that's always on and can reach the receiver, e.g. the one that runs your DVR. macOS, Linux or Windows, with **Python 3.11 or newer**.
- At least one of:
  - **Channels DVR**
  - **Jellyfin** (free)
  - **Plex** with **Plex Pass**, plus **Threadfin** (free), because Plex can't read channel lists on its own

## Set it up

### 1. Install it

If you don't have [pipx](https://pipx.pypa.io/stable/installation/) yet, install it first (on a Mac: `brew install pipx`). Then:

```bash
pipx install git+https://github.com/Matt-Hadley/sat_to_ip_helpers
```

### 2. Get the example config

Everything lives in one file. Make a folder for it and download the example:

```bash
mkdir ~/sat-to-ip && cd ~/sat-to-ip
curl -fsSLO https://raw.githubusercontent.com/Matt-Hadley/sat_to_ip_helpers/main/examples/sat-to-ip.toml
```

### 3. Fill in the settings

Open `sat-to-ip.toml`. Every setting has a comment; usually you only change these:

| Section | What to set |
|---|---|
| `[receiver]` | `host`: your receiver's address, e.g. `octopus.local` or `192.168.1.50` |
| `[[publishers]]` | Keep one block per app you use and delete the others. The [app guides](#4-prepare-your-apps) say what to put in each. |

Passwords never go in this file. The config only names a setting such as `OCTOPUS_PASSWORD`, and you set its value in your terminal:

```bash
export OCTOPUS_PASSWORD='your-receiver-password'
export JELLYFIN_API_KEY='your-jellyfin-api-key'    # only if you use Jellyfin
```

Then check the file: `sat-to-ip check-config -c sat-to-ip.toml`. It points at the exact setting if something's wrong, and notes anything that would trip up the first run (e.g. an output folder it can't write to).

### 4. Prepare your apps

Each app needs a one-time setting so it knows where its channels come from:

- **Channels DVR:** [docs/setup/channels-dvr.md](docs/setup/channels-dvr.md)
- **Jellyfin:** [docs/setup/jellyfin.md](docs/setup/jellyfin.md)
- **Plex (through Threadfin):** [docs/setup/plex-threadfin.md](docs/setup/plex-threadfin.md)

### 5. Choose your channels

```bash
sat-to-ip run -c sat-to-ip.toml --stages refresh   # first time only: scan the satellite
sat-to-ip channels -c sat-to-ip.toml
```

This shows every channel your receiver has found. Tick the ones you want with the space bar (`t` ticks all TV, `r` all radio) and press Enter. It then asks for the number of your first TV and first radio channel (e.g. 101 and 801) and saves your choice at the end of `sat-to-ip.toml`. Run it again whenever you want to change your channels; the ones you already have stay ticked, in the same order.

You can also edit the list by hand. It's a few blocks, each starting at a channel number:

```toml
[[channels]]
start = 101                     # BBC One HD is 101, ITV1 HD 102, Channel 4 HD 103
names = [
  "BBC One HD",
  "ITV1 HD",
  "Channel 4 HD",
]

[[channels]]
start = 801
names = [
  "BBC Radio 4",
  { rest = "radio" },           # every other radio channel
]
```

- **Names** must match your receiver exactly. If a name appears twice (e.g. two regions of a channel), write `{ name = "BBC One HD", frequency = 10773 }` with the frequency in MHz.
- **`{ rest = "tv" }`**, `"radio"` or `"all"` takes every channel of that kind you haven't already listed.
- **Numbers stay put:** if a channel disappears from the satellite, its number is kept free and the channels after it don't move.

### 6. Check, try, run

```bash
sat-to-ip check-config -c sat-to-ip.toml      # checks your settings, connects to nothing
sat-to-ip run -c sat-to-ip.toml --dry-run     # does everything except change anything
sat-to-ip run -c sat-to-ip.toml               # the real thing
```

At the end of every run there's a short summary with one line per step and per app:

```
  refresh                 ok
  select                  ok       76 channels (+2 / -0)
  publish:channels_dvr    ok       74 channels; tv: 65 → SatTV; radio: 9 → SatRadio
  publish:jellyfin        ok       76 channels; …
```

## Keeping your channels safe

The tool runs unattended, so it refuses to make a change that looks like a mistake. When that happens it changes nothing, shows the reason in the summary, and exits with an error so a scheduler can alert you.

| It won't… | …which protects you when |
|---|---|
| remove more than 5 of your saved channels in one go (you can change the 5 with `max_removed`) | rain or snow spoils the scan and fewer channels are found |
| save an empty channel list | your channel list doesn't match anything, e.g. after a typo |
| update an app from a guide that matches fewer than half the channels (`min_guide_match`) | the guide website is down or has changed |
| empty an app's TV or radio list | the guide matches the radio channels but none of the TV ones, or the other way round |

If one app can't be updated, the others still are.

After you've deliberately removed several channels from your list, run once with `--allow-removals`.

## Run it automatically every week

Any scheduler works. On a Mac or Linux, `crontab -e` and add a line like this one (Sundays at 4am):

```
0 4 * * 0 cd ~/sat-to-ip && . ./secrets.env && ~/.local/bin/sat-to-ip run -c sat-to-ip.toml >> run.log 2>&1
```

Here `secrets.env` holds your `export OCTOPUS_PASSWORD=…` lines. Make it private with `chmod 600 secrets.env`.

The scan briefly takes over the receiver's tuners, so pick a time when nothing is recording. A GitHub Actions workflow on a self-hosted runner works too; keep your config in a private repo.

## Everyday commands

| I want to… | Command |
|---|---|
| run everything | `sat-to-ip run -c sat-to-ip.toml` |
| see what would happen, without changing anything | `sat-to-ip run -c sat-to-ip.toml --dry-run` |
| just resend the channels to my apps, no rescan | `sat-to-ip run -c sat-to-ip.toml --stages publish` |
| change which channels I have | `sat-to-ip channels -c sat-to-ip.toml`, then `run` |
| apply a channel list I've cut down on purpose | `sat-to-ip run -c sat-to-ip.toml --allow-removals` |
| check my settings | `sat-to-ip check-config -c sat-to-ip.toml` |
| see more detail | add `-v` before `run` |

## Troubleshooting

**"Config error: …"** The message names the exact setting, e.g. `publishers[1].output_dir: required` means the second `[[publishers]]` block is missing `output_dir`. Unknown settings are errors too, so a typo can't be silently ignored.

**A channel has no TV guide.** The run lists every channel without a guide match, and leaves it out of that app. Most channels match by name automatically. For the rest, add the guide's ID for it under the guide in `sat-to-ip.toml`:

```toml
[guides.freeview.extra_mappings]
"ITV1 HD" = "ITV1Granada.uk"
"Channel 4 HD" = "Channel4North.uk"
```

The name on the left is the channel name from your list; the ID on the right is the guide's own ID for it, which you'll find in the guide file. ITV1 and Channel 4 come in regional versions, so these two lines are the usual ones to add, with your own region.

**Channel numbers changed.** Numbers only move when you change your channel list; each block's `start` sets where it begins (see [Choose your channels](#5-choose-your-channels)). In Channels DVR, also add `channel_numbers = "list"` to its publisher, otherwise Channels numbers the channels itself.

**"Refusing to remove N saved channels".** The scan found fewer channels than you have saved. Check the dish and try again later; if you really meant to drop them, run with `--allow-removals`.

**Jellyfin shows the old guide.** Jellyfin keeps a downloaded guide for up to an hour; it'll update on its next refresh.

**It ran but nothing changed.** Look at the summary: each line says `ok`, `failed` or `skipped`, with the reason. The command exits with `0` when everything worked, `1` when something failed, and `2` when the settings are wrong.

## Help and contributing

Found a channel that won't match, or want support for another receiver or app? Open an issue or a pull request. [CONTRIBUTING.md](CONTRIBUTING.md) explains how the code fits together and how to add things.

Guide data for Jellyfin and Plex comes from [Freeview-EPG](https://github.com/dp247/Freeview-EPG), a free community feed for personal use. Channels DVR uses its own Gracenote guide data.
