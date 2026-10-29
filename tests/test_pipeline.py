import logging
from pathlib import Path

import pytest

from sat_to_ip import pipeline
from sat_to_ip.checks import CheckSettings
from sat_to_ip.config import Config
from sat_to_ip.pipeline import RunOptions, Stage
from sat_to_ip.selection import specs_from_config
from tests.fakes import BasicReceiver, FakeGuide, FakePublisher, FakeReceiver, channel


def _blocks(specs: str):
    """Old one-per-line notation, for brevity: 'N = name' starts a block; tv/radio/all mean rest."""
    blocks: list[dict] = []
    for line in filter(None, (raw.strip() for raw in specs.splitlines())):
        start, _, entry = line.rpartition(" = ") if " = " in line else (None, "", line)
        value = {"rest": entry} if entry in ("tv", "radio", "all") else entry
        if start or not blocks:
            blocks.append({"start": int(start)} if start else {})
            blocks[-1]["names"] = []
        blocks[-1]["names"].append(value)
    return blocks


def _config(tmp_path: Path, receiver=None, guides=None, publishers=None, specs="Ch 1\nCh 2\nCh 3\n", **checks):
    return Config(
        receiver=receiver or FakeReceiver(),
        guides={"guide": FakeGuide()} if guides is None else guides,
        publishers=tuple(publishers or [FakePublisher()]),
        checks=CheckSettings(**checks),
        path=tmp_path / "c.toml",
        channel_specs=tuple(specs_from_config(_blocks(specs))) if specs is not None else None,
    )


def _statuses(report):
    return {o.name: o.status for o in report.outcomes}


def test_full_run_refreshes_selects_and_publishes(tmp_path):
    rx, pub = FakeReceiver(found=[channel(4)]), FakePublisher()
    report = pipeline.run(_config(tmp_path, receiver=rx, publishers=[pub], specs="Ch 1\nCh 2\nCh 3\nCh 4\n"))
    assert report.ok and report.exit_code == 0
    assert _statuses(report) == {"refresh": "ok", "select": "ok", "publish:pub": "ok"}
    assert rx.refreshed == 1 and len(rx.saved) == 1
    lineup, match, dry = pub.published[0]
    assert len(lineup) == 3 and len(match.refs) == 3 and dry is False


def test_unchanged_selection_is_not_saved_again(tmp_path):
    rx = FakeReceiver()
    report = pipeline.run(_config(tmp_path, receiver=rx), RunOptions(stages=frozenset({Stage.SELECT})))
    assert report.outcomes[0].detail == "3 channels, unchanged" and rx.saved == []


def test_stages_option_runs_only_those(tmp_path):
    rx = FakeReceiver()
    report = pipeline.run(_config(tmp_path, receiver=rx), RunOptions(stages=frozenset({Stage.PUBLISH})))
    assert _statuses(report) == {"publish:pub": "ok"}
    assert rx.refreshed == 0 and rx.saved == []


def test_receiver_without_capabilities_skips_those_stages(tmp_path):
    report = pipeline.run(_config(tmp_path, receiver=BasicReceiver()))
    assert _statuses(report) == {"refresh": "skipped", "select": "skipped", "publish:pub": "ok"}
    assert report.ok


def test_select_skipped_without_channel_blocks(tmp_path):
    report = pipeline.run(_config(tmp_path, specs=None), RunOptions(stages=frozenset({Stage.SELECT})))
    assert report.outcomes[0].status == "skipped" and "no [[channels]]" in report.outcomes[0].detail


def test_refresh_failure_aborts_before_publishing(tmp_path):
    pub = FakePublisher()
    report = pipeline.run(_config(tmp_path, receiver=FakeReceiver(fail_on="refresh"), publishers=[pub]))
    assert _statuses(report) == {"refresh": "failed", "publish": "skipped"}
    assert "RuntimeError: refresh broke" in report.outcomes[0].detail
    assert pub.published == [] and report.exit_code == 1


def test_removal_check_aborts_and_saves_nothing(tmp_path):
    rx = FakeReceiver(channels=[channel(i) for i in range(1, 11)])
    report = pipeline.run(_config(tmp_path, receiver=rx, specs="Ch 1\n", max_removed=5))
    assert _statuses(report)["select"] == "refused" and report.exit_code == pipeline.ExitCode.REFUSED
    assert "Refusing to remove 9" in report.outcomes[1].detail
    assert rx.saved == []


def test_allow_removals_overrides_check(tmp_path):
    rx = FakeReceiver(channels=[channel(i) for i in range(1, 11)])
    report = pipeline.run(_config(tmp_path, receiver=rx, specs="Ch 1\n"), RunOptions(allow_removals=True))
    assert report.ok and len(rx.saved[0]) == 1


def test_select_logs_changes_and_unmatched_specs(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    pipeline.run(_config(tmp_path, specs="Ch 1\nCh 2\nNope\n"), RunOptions(stages=frozenset({Stage.SELECT})))
    assert "- Ch 3" in caplog.text and "No channel matches 'Nope'" in caplog.text


def test_dry_run_changes_nothing(tmp_path):
    rx, pub = FakeReceiver(found=[channel(4)]), FakePublisher()
    config = _config(tmp_path, receiver=rx, publishers=[pub], specs="Ch 1\nCh 2\nCh 3\nCh 4\n")
    report = pipeline.run(config, RunOptions(dry_run=True))
    assert report.ok and report.outcomes[1].detail == "dry run: would save 4 channels"
    assert rx.refreshed == 0 and rx.saved == []
    assert _statuses(report)["refresh"] == "skipped"
    assert pub.published[0][2] is True


def test_empty_lineup_is_never_published(tmp_path):
    pub = FakePublisher()
    report = pipeline.run(_config(tmp_path, receiver=FakeReceiver(channels=[]), publishers=[pub], specs=None))
    assert _statuses(report)["publish"] == "refused" and pub.published == []


def test_lineup_error_fails_publish(tmp_path):
    report = pipeline.run(
        _config(tmp_path, receiver=FakeReceiver(fail_on="lineup")), RunOptions(stages=frozenset({Stage.PUBLISH}))
    )
    assert report.outcomes == [pipeline.Outcome("publish", "failed", "RuntimeError: lineup broke")]


def test_failed_guide_fails_only_its_publishers(tmp_path):
    good = FakePublisher(name="good", guide="ok")
    bad = FakePublisher(name="bad", guide="broken")
    no_guide = FakePublisher(name="plain", guide=None)
    guides = {"ok": FakeGuide(name="ok"), "broken": FakeGuide(name="broken", fail=True)}
    report = pipeline.run(
        _config(tmp_path, guides=guides, publishers=[good, bad, no_guide]),
        RunOptions(stages=frozenset({Stage.PUBLISH})),
    )
    assert _statuses(report) == {"publish:good": "ok", "publish:bad": "failed", "publish:plain": "ok"}
    assert "guide 'broken': RuntimeError: feed down" in report.outcomes[1].detail
    assert bad.published == [] and no_guide.published[0][1] is None
    assert report.exit_code == 1


def test_guide_below_minimum_match_blocks_its_publishers(tmp_path):
    pub = FakePublisher()
    guides = {"guide": FakeGuide(unmatched_names=frozenset({"Ch 1", "Ch 2"}))}
    report = pipeline.run(
        _config(tmp_path, guides=guides, publishers=[pub], min_guide_match=0.5),
        RunOptions(stages=frozenset({Stage.PUBLISH})),
    )
    assert "matched only 1/3" in report.outcomes[0].detail and pub.published == []
    assert report.outcomes[0].status == "refused" and report.exit_code == pipeline.ExitCode.REFUSED


def test_a_failure_outranks_a_refusal_in_the_exit_code():
    report = pipeline.RunReport()
    report.add("publish:a", "refused", "check")
    assert report.exit_code == pipeline.ExitCode.REFUSED
    report.add("publish:b", "failed", "unreachable")
    assert report.exit_code == pipeline.ExitCode.FAILED and not report.ok


def test_shared_guide_matches_once(tmp_path):
    guide = FakeGuide()
    pubs = [FakePublisher(name="a"), FakePublisher(name="b")]
    pipeline.run(
        _config(tmp_path, guides={"guide": guide}, publishers=pubs), RunOptions(stages=frozenset({Stage.PUBLISH}))
    )
    assert guide.calls == 1


def test_one_publisher_failing_does_not_stop_the_next(tmp_path, caplog):
    first, second = FakePublisher(name="first", fail=True), FakePublisher(name="second")
    report = pipeline.run(_config(tmp_path, publishers=[first, second]), RunOptions(stages=frozenset({Stage.PUBLISH})))
    assert _statuses(report) == {"publish:first": "failed", "publish:second": "ok"}
    assert len(second.published) == 1


def test_unmatched_channels_are_logged(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    guides = {"guide": FakeGuide(unmatched_names=frozenset({"Ch 3"}))}
    pipeline.run(_config(tmp_path, guides=guides), RunOptions(stages=frozenset({Stage.PUBLISH})))
    assert "no match for 'Ch 3'" in caplog.text


def test_report_summary_lists_every_outcome(tmp_path):
    report = pipeline.run(_config(tmp_path))
    lines = report.summary().splitlines()
    assert [line.split()[0] for line in lines] == ["refresh", "select", "publish:pub"]


@pytest.mark.parametrize("stage", ["select"])
def test_select_failure_aborts(tmp_path, stage):
    report = pipeline.run(
        _config(tmp_path, receiver=FakeReceiver(fail_on=stage)),
        RunOptions(stages=frozenset({Stage.SELECT, Stage.PUBLISH})),
    )
    assert _statuses(report) == {"select": "failed", "publish": "skipped"}


@pytest.mark.parametrize("options", [RunOptions(), RunOptions(allow_removals=True)])
def test_selection_matching_nothing_never_empties_the_receiver(tmp_path, options):
    rx = FakeReceiver()
    report = pipeline.run(_config(tmp_path, receiver=rx, specs="Renamed\n", max_removed=50), options)
    assert "matches no channels" in report.outcomes[1].detail and rx.saved == []


def test_added_channels_are_logged(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    rx = FakeReceiver(channels=[channel(1)], found=[channel(2, "New")])
    pipeline.run(_config(tmp_path, receiver=rx, specs="Ch 1\nNew\n"), RunOptions(stages=frozenset({Stage.SELECT})))
    assert "+ New" in caplog.text


def test_publish_numbers_the_lineup_from_the_channel_list(tmp_path):
    pub = FakePublisher(guide=None)
    rx = FakeReceiver(channels=[channel(2, "B"), channel(1, "A"), channel(3, "R", kind="radio")])
    pipeline.run(
        _config(tmp_path, receiver=rx, publishers=[pub], specs="101 = A\nGone\nB\n801 = R\n"),
        RunOptions(stages=frozenset({Stage.PUBLISH})),
    )
    assert [(c.number, c.name) for c in pub.published[0][0]] == [(101, "A"), (103, "B"), (801, "R")]


def test_number_clash_fails_publish(tmp_path):
    rx = FakeReceiver(channels=[channel(1, "A"), channel(2, "B"), channel(3, "C")])
    report = pipeline.run(
        _config(tmp_path, receiver=rx, specs="tv\n2 = C\n"), RunOptions(stages=frozenset({Stage.PUBLISH}))
    )
    assert (
        report.outcomes[0] == pipeline.Outcome("publish", "refused", report.outcomes[0].detail)
        and "already taken" in report.outcomes[0].detail
    )


def test_publish_refuses_when_many_saved_channels_have_no_stream(tmp_path):
    rx = FakeReceiver(
        channels=[channel(i) for i in range(1, 11)], unstreamable=frozenset(f"Ch {i}" for i in range(3, 11))
    )
    pub = FakePublisher(guide=None)
    report = pipeline.run(
        _config(tmp_path, receiver=rx, publishers=[pub], max_removed=5), RunOptions(stages=frozenset({Stage.PUBLISH}))
    )
    assert report.outcomes[0].status == "refused" and "8 saved channels have no stream" in report.outcomes[0].detail
    assert pub.published == []


def test_publish_allows_a_few_missing_streams_or_allow_removals(tmp_path):
    rx = FakeReceiver(
        channels=[channel(i) for i in range(1, 11)], unstreamable=frozenset(f"Ch {i}" for i in range(3, 11))
    )
    pub = FakePublisher(guide=None)
    options = RunOptions(stages=frozenset({Stage.PUBLISH}), allow_removals=True)
    assert pipeline.run(_config(tmp_path, receiver=rx, publishers=[pub]), options).ok
    assert len(pub.published[0][0]) == 2


def test_publish_refuses_duplicate_keys(tmp_path):
    rx = FakeReceiver(channels=[channel(1), channel(1, "Twin")])
    report = pipeline.run(_config(tmp_path, receiver=rx, specs=None), RunOptions(stages=frozenset({Stage.PUBLISH})))
    assert "same key: fake:1" in report.outcomes[0].detail
