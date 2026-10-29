"""model, checks, m3u, files, http, names, mappings, selection."""

import os
import stat
from unittest.mock import MagicMock, patch

import pytest
import requests

from sat_to_ip import checks, files, http, m3u, mappings
from sat_to_ip.exceptions import CheckError, ConfigError
from sat_to_ip.model import ChannelRef, GuideMatch, GuideRef, Lineup, SelectionPlan
from sat_to_ip.names import normalize_name
from tests.fakes import channel

# model ------------------------------------------------------------------------------------------


def test_lineup_views():
    lineup = Lineup((channel(1), channel(2, kind="radio")))
    assert len(lineup) == 2 and [c.number for c in lineup] == [1, 2]
    assert lineup.tv == (channel(1),) and lineup.radio == (channel(2, kind="radio"),)


def test_guide_match_ratio():
    assert GuideMatch("g", "k").match_ratio == 1.0
    m = GuideMatch("g", "k", {"a": GuideRef("x")}, (channel(2),))
    assert m.total == 2 and m.match_ratio == 0.5


def test_selection_plan_diff():
    a, b, c = ChannelRef("a", "A"), ChannelRef("b", "B"), ChannelRef("c", "C")
    plan = SelectionPlan(current=(a, b), proposed=(b, c))
    assert plan.removed == (a,) and plan.added == (c,)


# checks -----------------------------------------------------------------------------------------


def _plan(current: int, proposed: int) -> SelectionPlan:
    refs = tuple(ChannelRef(str(i), f"Ch {i}") for i in range(current))
    return SelectionPlan(refs, refs[:proposed])


def test_check_removals():
    checks.check_removals(_plan(10, 5), 5, allow=False)
    checks.check_removals(_plan(20, 0), 5, allow=True)
    with pytest.raises(CheckError, match=r"Refusing to remove 15 saved channels \(limit 5\): Ch 5, .* …"):
        checks.check_removals(_plan(20, 5), 5, allow=False)


def test_check_selection_not_empty():
    checks.check_selection_not_empty(_plan(3, 1))
    checks.check_selection_not_empty(_plan(0, 0))
    with pytest.raises(CheckError, match="refusing to replace the 3 saved ones"):
        checks.check_selection_not_empty(_plan(3, 0))


def test_check_lineup():
    checks.check_lineup(Lineup((channel(1), channel(2, kind="radio"))))
    with pytest.raises(CheckError, match="empty lineup"):
        checks.check_lineup(Lineup())
    with pytest.raises(CheckError, match="same key: fake:1"):
        checks.check_lineup(Lineup((channel(1), channel(1, "Other"))))
    with pytest.raises(CheckError, match="unknown kind 'video'"):
        checks.check_lineup(Lineup((channel(1, kind="video"),)))


def test_check_lineup_complete(caplog):
    saved = [ChannelRef(f"fake:{i}", f"Ch {i}") for i in range(1, 11)]
    checks.check_lineup_complete(saved, Lineup(tuple(channel(i) for i in range(1, 11))), 0, allow=False)
    checks.check_lineup_complete(saved, Lineup((channel(1),)), 9, allow=False)  # 9 missing, limit 9
    assert "'Ch 10' is saved on the receiver but it has no stream" in caplog.text
    checks.check_lineup_complete(saved, Lineup((channel(1),)), 0, allow=True)
    with pytest.raises(CheckError, match=r"9 saved channels have no stream on the receiver \(limit 5\): Ch 2"):
        checks.check_lineup_complete(saved, Lineup((channel(1),)), 5, allow=False)


def test_check_guide_match():
    checks.check_guide_match(GuideMatch("g", "k"), 0.9)
    checks.check_guide_match(GuideMatch("g", "k", {"a": GuideRef("x")}, (channel(2),)), 0.5)
    with pytest.raises(CheckError, match="matched only 1/2"):
        checks.check_guide_match(GuideMatch("g", "k", {"a": GuideRef("x")}, (channel(2),)), 0.6)


# m3u --------------------------------------------------------------------------------------------


def test_m3u_round_trip():
    text = (
        '#EXTM3U\n#EXTINF:-1 tvg-id="bbc1" tvg-name="BBC One",BBC One HD\nhttp://o/1\n#EXTINF:-1,Radio 4\nhttp://o/2\n'
    )
    entries = m3u.parse(text)
    assert entries[0] == m3u.Entry("BBC One HD", "http://o/1", (("tvg-id", "bbc1"), ("tvg-name", "BBC One")))
    assert entries[0].attr("tvg-id") == "bbc1" and entries[1].attr("tvg-id") is None
    assert m3u.render(entries) == text


def test_m3u_parse_skips_option_lines_and_handles_missing_url():
    entries = m3u.parse("#EXTM3U\n#EXTINF:-1,A\n#EXTVLCOPT:x=y\n\nhttp://a\n#EXTINF:-1,B\n")
    assert [(e.label, e.url) for e in entries] == [("A", "http://a"), ("B", "")]


def test_m3u_parse_comma_inside_a_quoted_attribute():
    entry = m3u.parse('#EXTINF:-1 tvg-name="A, B" tvg-id="x",Label\nhttp://a\n')[0]
    assert entry == m3u.Entry("Label", "http://a", (("tvg-name", "A, B"), ("tvg-id", "x")))


def test_m3u_parse_entry_without_url_doesnt_take_the_next_ones():
    entries = m3u.parse("#EXTM3U\n#EXTINF:-1,One\n#EXTINF:-1,Two\nhttp://two\n")
    assert [(e.label, e.url) for e in entries] == [("One", ""), ("Two", "http://two")]


def test_m3u_parse_extinf_without_a_comma():
    assert m3u.parse("#EXTINF:-1\nhttp://a\n")[0] == m3u.Entry("", "http://a")


def test_m3u_render_strips_double_quotes():
    assert "tvg-name=\"Say 'hi'\"" in m3u.render([m3u.Entry("x", "u", (("tvg-name", 'Say "hi"'),))])


def test_m3u_render_cannot_be_injected_into():
    hostile = m3u.Entry(
        "BBC\r\n#EXTINF:-1,Evil", "http://o/1\nhttp://evil", (("tvg-name", 'A\n#EXTINF:-1,X\nhttp://evil "q"'),)
    )
    text = m3u.render([hostile])
    lines = text.splitlines()
    assert len(lines) == 3 and sum(line.startswith("#EXTINF") for line in lines) == 1
    assert text.splitlines()[2].startswith("http://o/1")  # glued into one line, not a second entry


def test_m3u_render_strips_unicode_line_breaks():
    text = m3u.render([m3u.Entry("A\u2028#EXTINF:-1,B\x85C\u2029", "u")])
    assert len(text.splitlines()) == 3  # str.splitlines() breaks on these too


def test_m3u_render_empty():
    assert m3u.render([]) == "#EXTM3U\n"


# files ------------------------------------------------------------------------------------------


def test_write_atomic_creates_dirs_and_is_world_readable(tmp_path):
    target = tmp_path / "a" / "b.m3u"
    files.write_atomic(target, "hello")
    files.write_atomic(target, b"bytes")
    assert target.read_bytes() == b"bytes"
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o644
    assert [p.name for p in target.parent.iterdir()] == ["b.m3u"]


def test_write_atomic_keeps_an_existing_files_mode_and_follows_symlinks(tmp_path):
    real = tmp_path / "real.toml"
    real.write_text("old")
    real.chmod(0o600)
    link = tmp_path / "link.toml"
    link.symlink_to(real)
    files.write_atomic(link, "new")
    assert link.is_symlink() and real.read_text() == "new"
    assert stat.S_IMODE(os.stat(real).st_mode) == 0o600


def test_write_atomic_cleans_up_on_failure(tmp_path):
    with patch("sat_to_ip.files.os.replace", side_effect=OSError("disk")), pytest.raises(OSError):
        files.write_atomic(tmp_path / "x.txt", "data")
    assert list(tmp_path.iterdir()) == []


# http -------------------------------------------------------------------------------------------


def test_session_mounts_retrying_timeout_adapter():
    s = http.session(timeout=7, retries=2)
    adapter = s.get_adapter("https://example.org")
    assert adapter.timeout == 7
    assert adapter.max_retries.total == 2
    assert adapter.max_retries.allowed_methods == frozenset({"GET", "HEAD"})


def test_adapter_applies_default_timeout_only_when_unset():
    adapter = http._TimeoutAdapter(5)
    with patch.object(requests.adapters.HTTPAdapter, "send", return_value="r") as send:
        adapter.send(MagicMock())
        adapter.send(MagicMock(), timeout=1)
    assert send.call_args_list[0].kwargs["timeout"] == 5
    assert send.call_args_list[1].kwargs["timeout"] == 1


# names / mappings / selection -------------------------------------------------------------------


def test_normalize_name():
    assert normalize_name("BBC One HD") == "bbconehd" and normalize_name("") == ""


def test_builtin_gracenote_mappings_load():
    assert "uk" in mappings.available_regions("gracenote")
    uk = mappings.load_mappings("gracenote", "uk")
    assert len(uk) > 100 and all(k == k.lower() for k in uk)
    assert uk["5select"] == "5DRAMA"  # Gracenote renamed station 56888; the satellite still says 5SELECT
    assert uk["radio x"] == "XFM" and uk["tlc hd"] == "TLCUKHD"  # radio.csv and tv.csv both load
    assert uk["great!christmas"] == uk["great! romance"] == "GREATRO"  # its seasonal name


def test_extra_mappings_win_and_unknown_region():
    assert mappings.load_mappings("gracenote", "uk", {"4Seven": "MINE"})["4seven"] == "MINE"
    assert mappings.load_mappings("gracenote", None, {"4Seven": "MINE"}) == {"4seven": "MINE"}
    assert mappings.available_regions("nope") == []
    with pytest.raises(ConfigError, match="No built-in gracenote mappings for region 'mars'"):
        mappings.load_mappings("gracenote", "mars")


@pytest.mark.parametrize("content", ["Name,Callsign\nA,B\n", ""])
def test_mappings_bad_header(tmp_path, content):
    bad = tmp_path / "bad.csv"
    bad.write_text(content)
    with pytest.raises(ConfigError, match="expected a header row 'Name,GuideId'"):
        mappings.load_csv(bad)


def test_mappings_unreadable(tmp_path):
    with pytest.raises(ConfigError, match="Can't read mappings file"):
        mappings.load_csv(tmp_path / "missing.csv")
