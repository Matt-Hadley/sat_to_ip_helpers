import os

import pytest

from sat_to_ip import m3u
from sat_to_ip.file_output import write_lineup_files
from sat_to_ip.model import GuideMatch, GuideRef, Lineup
from tests.fakes import channel

XMLTV = b'<?xml version="1.0"?><tv><channel id="bbc1.uk"/></tv>'


def _lineup():
    return Lineup((channel(1, "BBC One"), channel(2, "Unmatched"), channel(3, "Radio 4", kind="radio")))


def _guide(xmltv=XMLTV):
    refs = {"fake:1": GuideRef("bbc1.uk", "BBC One HD"), "fake:3": GuideRef("radio4.uk")}
    return GuideMatch("freeview", "xmltv", refs, (channel(2, "Unmatched"),), xmltv)


def test_writes_matched_channels_with_attributes(tmp_path):
    out = write_lineup_files(tmp_path, _lineup(), _guide(), dry_run=False)

    assert out.directory == tmp_path and out.counts == {"tv": 1, "radio": 1} and out.channels == 2
    assert {p.name for p in out.paths} == {"tv.m3u", "radio.m3u", "guide.xml"}
    tv = m3u.parse((tmp_path / "tv.m3u").read_text())
    assert [(e.label, e.url, e.attrs) for e in tv] == [
        (
            "BBC One",
            "http://rx/1",
            (("tvg-id", "bbc1.uk"), ("tvg-name", "BBC One HD"), ("tvg-chno", "1"), ("group-title", "TV")),
        )
    ]
    radio = m3u.parse((tmp_path / "radio.m3u").read_text())
    assert radio[0].attr("group-title") == "Radio" and radio[0].attr("tvg-name") == "Radio 4"
    assert (tmp_path / "guide.xml").read_bytes() == XMLTV


def test_without_guide_writes_every_channel_and_no_tvg_id(tmp_path):
    out = write_lineup_files(tmp_path, _lineup(), None, dry_run=False)
    assert out.counts == {"tv": 2, "radio": 1}
    assert not (tmp_path / "guide.xml").exists()
    assert all(e.attr("tvg-id") is None for e in m3u.parse((tmp_path / "tv.m3u").read_text()))


def test_guide_without_xmltv_writes_no_guide_file(tmp_path):
    write_lineup_files(tmp_path, _lineup(), _guide(xmltv=None), dry_run=False)
    assert not (tmp_path / "guide.xml").exists()


def test_empty_radio_playlist_is_still_written(tmp_path):
    out = write_lineup_files(tmp_path, Lineup((channel(1),)), None, dry_run=False)
    assert out.counts["radio"] == 0
    assert (tmp_path / "radio.m3u").read_text() == "#EXTM3U\n"


def test_dry_run_writes_only_to_a_temp_dir(tmp_path):
    target = tmp_path / "out"
    out = write_lineup_files(target, _lineup(), _guide(), dry_run=True)
    assert not target.exists()
    assert out.directory != target and (out.directory / "tv.m3u").exists() and (out.directory / "guide.xml").exists()


def test_failed_write_leaves_old_file_and_no_partial(tmp_path, monkeypatch):
    (tmp_path / "tv.m3u").write_text("old")

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        write_lineup_files(tmp_path, _lineup(), None, dry_run=False)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["tv.m3u"]
    assert (tmp_path / "tv.m3u").read_text() == "old"
