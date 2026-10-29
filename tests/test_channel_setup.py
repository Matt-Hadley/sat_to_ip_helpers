import tomllib

import pytest

from sat_to_ip import channel_setup, cli, registry
from sat_to_ip.config import load_config
from sat_to_ip.exceptions import ConfigError, IntegrationError
from tests.fakes import BasicReceiver, FakeReceiver, channel

CONFIG = """# My setup
[receiver]
type = "fake"   # keep me

{channels}
[[publishers]]
type = "fake"
"""


@pytest.fixture(autouse=True)
def fake_builders(monkeypatch):
    monkeypatch.setitem(registry.BUILDERS["receiver"], "fake", "tests.fakes:build_receiver")
    monkeypatch.setitem(registry.BUILDERS["publisher"], "fake", "tests.fakes:build_publisher")


def _config(tmp_path, channels="", receiver=None):
    path = tmp_path / "sat-to-ip.toml"
    path.write_text(CONFIG.format(channels=channels))
    config = load_config(path, {})
    if receiver is not None:
        object.__setattr__(config, "receiver", receiver)
    return config


def _run(config, chosen=lambda ticked, others: ticked + others, answers=("", "")):
    replies, said, seen = iter(answers), [], {}

    def pick(ticked, others):
        seen["ticked"], seen["others"] = [c.name for c in ticked], [c.name for c in others]
        return chosen(ticked, others)

    saved = channel_setup.choose_channels(config, pick=pick, ask=lambda q: next(replies), say=said.append)
    return saved, said, seen


def _blocks(config):
    return tomllib.loads(config.path.read_text())["channels"]


def test_first_run_ticks_the_receivers_saved_channels_and_writes_tv_and_radio_blocks(tmp_path):
    rx = FakeReceiver(channels=[channel(1), channel(3, kind="radio")], found=[channel(2)])
    config = _config(tmp_path, receiver=rx)
    saved, said, seen = _run(config)
    assert saved and seen == {"ticked": ["Ch 1", "Ch 3"], "others": ["Ch 2"]}
    assert _blocks(config) == [{"start": 101, "names": ["Ch 1", "Ch 2"]}, {"start": 801, "names": ["Ch 3"]}]
    assert said[0].startswith("Saved 3 channels") and "(TV 101–102, radio 801–801)" in said[0]
    text = config.path.read_text()
    assert text.startswith('# My setup\n[receiver]\ntype = "fake"   # keep me\n')
    load_config(config.path, {})  # still a valid config


def test_existing_blocks_keep_their_order_and_starts_and_new_channels_follow(tmp_path):
    rx = FakeReceiver(channels=[channel(1), channel(2), channel(3, kind="radio")], found=[channel(4)])
    blocks = '[[channels]]\nstart = 50\nnames = ["Ch 2", "Ch 1"]\n\n[[channels]]\nstart = 700\nnames = ["Ch 3"]\n'
    config = _config(tmp_path, blocks, receiver=rx)
    saved, _, seen = _run(config)
    assert seen["ticked"] == ["Ch 2", "Ch 1", "Ch 3"]
    assert _blocks(config) == [{"start": 50, "names": ["Ch 2", "Ch 1", "Ch 4"]}, {"start": 700, "names": ["Ch 3"]}]
    assert config.path.read_text().index("[[channels]]") < config.path.read_text().index("[[publishers]]")


def test_unticking_drops_channels_and_typed_numbers_are_used(tmp_path):
    rx = FakeReceiver(channels=[channel(1), channel(2), channel(3, kind="radio")])
    config = _config(tmp_path, receiver=rx)
    _run(
        config,
        chosen=lambda ticked, others: [c for c in ticked if c.name != "Ch 2"],
        answers=("200", "x", "150", "300"),
    )
    assert _blocks(config) == [{"start": 200, "names": ["Ch 1"]}, {"start": 300, "names": ["Ch 3"]}]


def test_radio_only(tmp_path):
    rx = FakeReceiver(channels=[channel(3, kind="radio")])
    config = _config(tmp_path, receiver=rx)
    _run(config, answers=("",))
    assert _blocks(config) == [{"start": 801, "names": ["Ch 3"]}]


def test_radio_default_follows_a_long_tv_block(tmp_path):
    rx = FakeReceiver(channels=[channel(i) for i in range(1, 6)] + [channel(9, kind="radio")])
    config = _config(tmp_path, receiver=rx)
    _run(config, answers=("798", ""))
    assert _blocks(config)[1]["start"] == 901  # 798–802 is TV, so radio starts at the next round number


def test_same_name_twice_is_written_with_its_frequency(tmp_path):
    from sat_to_ip.model import Channel

    twins = [
        Channel("a", "BBC One HD", "tv", "u", frequency_mhz=10773.0),
        Channel("b", "BBC One HD", "tv", "u", frequency_mhz=10847.0),
    ]
    config = _config(tmp_path, receiver=FakeReceiver(channels=twins))
    _run(config, answers=("",))
    assert _blocks(config)[0]["names"] == [
        {"name": "BBC One HD", "frequency": 10773.0},
        {"name": "BBC One HD", "frequency": 10847.0},
    ]


@pytest.mark.parametrize(
    "chosen, message", [(lambda t, o: None, "Nothing saved."), (lambda t, o: [], "No channels ticked; nothing saved.")]
)
def test_cancel_or_nothing_ticked_changes_nothing(tmp_path, chosen, message):
    config = _config(tmp_path, receiver=FakeReceiver())
    before = config.path.read_text()
    saved, said, _ = _run(config, chosen=chosen)
    assert not saved and said == [message] and config.path.read_text() == before


def test_channel_blocks_edited_while_picking_saves_nothing(tmp_path):
    config = _config(tmp_path, '[[channels]]\nstart = 101\nnames = ["BBC One HD"]\n', receiver=FakeReceiver())

    def edit_then_pick(ticked, others):
        config.path.write_text(config.path.read_text().replace('"BBC One HD"', '"ITV1 HD", "BBC One HD"'))
        return ticked + others

    with pytest.raises(ConfigError, match="changed while picking"):
        _run(config, chosen=edit_then_pick)
    assert '"ITV1 HD", "BBC One HD"' in config.path.read_text()  # the user's edit is left as it was


def test_ctrl_d_at_a_number_prompt_saves_nothing(tmp_path):
    config = _config(tmp_path, receiver=FakeReceiver())
    before = config.path.read_text()

    def eof(_):
        raise EOFError

    assert not channel_setup.choose_channels(config, pick=lambda t, o: t, ask=eof, say=lambda m: None)
    assert config.path.read_text() == before


def test_receiver_with_no_channels_says_to_scan_first_and_opens_no_picker(tmp_path):
    config = _config(tmp_path, receiver=FakeReceiver(channels=[]))
    with pytest.raises(IntegrationError, match="no channels yet; scan first"):
        channel_setup.choose_channels(config, pick=lambda t, o: pytest.fail("picker opened"), ask=input, say=print)


def test_receiver_without_a_channel_list(tmp_path):
    config = _config(tmp_path, receiver=BasicReceiver())
    with pytest.raises(ConfigError, match="can't list its channels"):
        _run(config)


# cli ----------------------------------------------------------------------------------------------


def test_cli_needs_a_terminal(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["channels", "-c", str(config.path)]) == 2


@pytest.mark.parametrize(
    "error, code", [(ConfigError("no"), 2), (IntegrationError("receiver down"), 1), (RuntimeError("surprise"), 1)]
)
def test_cli_runs_the_picker_and_maps_errors(tmp_path, monkeypatch, error, code):
    config = _config(tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def boom(_config):
        raise error

    monkeypatch.setattr(channel_setup, "choose_channels", boom)
    assert cli.main(["channels", "-c", str(config.path)]) == code
    monkeypatch.setattr(channel_setup, "choose_channels", lambda _config: True)
    assert cli.main(["channels", "-c", str(config.path)]) == 0


# editing existing blocks ----------------------------------------------------------------------------


def test_editing_keeps_every_block_start_and_missing_channels_so_numbers_stay_put(tmp_path):
    rx = FakeReceiver(channels=[channel(1), channel(2), channel(3, kind="radio")])
    blocks = (
        '[[channels]]\nstart = 101\nnames = ["Gone HD", "Ch 1"]\n\n'
        '[[channels]]\nstart = 201\nnames = ["Ch 2"]\n\n'
        '[[channels]]\nstart = 801\nnames = ["Ch 3"]\n'
    )
    config = _config(tmp_path, blocks, receiver=rx)
    saved, said, _ = _run(config, answers=())  # nothing new, so nothing is asked
    assert saved and _blocks(config) == [
        {"start": 101, "names": ["Gone HD", "Ch 1"]},
        {"start": 201, "names": ["Ch 2"]},
        {"start": 801, "names": ["Ch 3"]},
    ]
    assert "(TV 101–102, TV 201–201, radio 801–801)" in said[0] and said[1].startswith("Kept 1 listed")


def test_rest_is_kept_while_all_its_channels_are_ticked_and_listed_by_name_once_one_is_not(tmp_path):
    rx = FakeReceiver(channels=[channel(1), channel(2)], found=[channel(3)])
    config = _config(tmp_path, '[[channels]]\nstart = 1\nnames = [{ rest = "tv" }]\n', receiver=rx)
    _run(config, answers=())
    assert _blocks(config) == [{"start": 1, "names": [{"rest": "tv"}]}]
    _run(config, chosen=lambda ticked, others: [c for c in ticked if c.name != "Ch 2"], answers=())
    assert _blocks(config) == [{"start": 1, "names": ["Ch 1", "Ch 3"]}]


def test_unticked_entries_go_and_an_emptied_block_is_dropped(tmp_path):
    rx = FakeReceiver(channels=[channel(1), channel(3, kind="radio")])
    blocks = '[[channels]]\nstart = 101\nnames = ["Ch 1"]\n\n[[channels]]\nstart = 801\nnames = ["Ch 3"]\n'
    config = _config(tmp_path, blocks, receiver=rx)
    _run(config, chosen=lambda ticked, others: [c for c in ticked if c.kind == "radio"], answers=())
    assert _blocks(config) == [{"start": 801, "names": ["Ch 3"]}]


def test_a_new_kind_gets_its_own_block_and_a_block_without_start_stays_without(tmp_path):
    rx = FakeReceiver(channels=[channel(1)], found=[channel(3, kind="radio")])
    config = _config(tmp_path, '[[channels]]\nnames = ["Ch 1"]\n', receiver=rx)
    saved, said, _ = _run(config, answers=("",))
    assert _blocks(config) == [{"names": ["Ch 1"]}, {"start": 801, "names": ["Ch 3"]}]
    assert "(TV, radio 801–801)" in said[0]


def test_ctrl_d_when_asked_for_a_new_blocks_start_saves_nothing(tmp_path):
    rx = FakeReceiver(channels=[channel(1)], found=[channel(3, kind="radio")])
    config = _config(tmp_path, '[[channels]]\nstart = 1\nnames = ["Ch 1"]\n', receiver=rx)
    before = config.path.read_text()

    def eof(_):
        raise EOFError

    assert not channel_setup.choose_channels(config, pick=lambda t, o: t + o, ask=eof, say=lambda m: None)
    assert config.path.read_text() == before
