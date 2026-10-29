"""Behaviour every GuideSource and Publisher must have. Call these from each integration's tests."""

from sat_to_ip.checks import check_lineup
from sat_to_ip.interfaces import GuideSource, Publisher, Receiver, SupportsSelection
from sat_to_ip.model import GuideMatch, Lineup, PublishResult


def assert_guide_contract(guide: GuideSource, lineup: Lineup) -> GuideMatch:
    match = guide.match(lineup)
    assert match.source == guide.name and match.kind == guide.kind
    keys = {ch.key for ch in lineup}
    assert set(match.refs) <= keys, "refs must be keyed by Channel.key"
    assert set(match.refs).isdisjoint({ch.key for ch in match.unmatched}), "a channel is matched or unmatched"
    assert len(match.refs) + len(match.unmatched) == len(lineup), "every channel is accounted for"
    return match


def assert_publisher_contract(publisher: Publisher, lineup: Lineup, guide: GuideMatch | None) -> PublishResult:
    """Dry run must succeed, report the target, and count only what would be published."""
    if publisher.guide is not None:
        assert guide is None or guide.kind in publisher.accepts_guides
    result = publisher.publish(lineup, guide, dry_run=True)
    assert result.target == publisher.name
    expected = len(guide.refs) if guide is not None else len(lineup)
    assert result.channels == expected, f"published {result.channels}, expected {expected}"
    return result


def assert_receiver_contract(receiver: Receiver) -> None:
    """What every receiver's lineup (and saved list, if it has one) must look like. Uses the real device stub."""
    lineup = receiver.lineup()
    check_lineup(lineup)  # not empty, unique keys, kind is tv/radio
    for ch in lineup:
        assert ch.number == 0, f"{ch.name}: leave number at 0; the pipeline numbers the lineup"
        assert ch.stream_url, f"{ch.name}: no stream_url"
    if isinstance(receiver, SupportsSelection):
        saved, found = receiver.selection_candidates()
        keys = [ch.key for ch in [*saved, *found]]
        assert len(keys) == len(set(keys)), "selection_candidates keys must be unique"
        assert {ch.key for ch in lineup} <= {ch.key for ch in saved}, "lineup keys must be the saved channels' keys"
