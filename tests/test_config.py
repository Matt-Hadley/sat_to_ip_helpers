from pathlib import Path

import pytest

from sat_to_ip import registry
from sat_to_ip.config import Section, load_config
from sat_to_ip.exceptions import ConfigError


@pytest.fixture(autouse=True)
def fake_builders(monkeypatch):
    monkeypatch.setitem(registry.BUILDERS["receiver"], "fake", "tests.fakes:build_receiver")
    monkeypatch.setitem(registry.BUILDERS["guide"], "fake", "tests.fakes:build_guide")
    monkeypatch.setitem(registry.BUILDERS["publisher"], "fake", "tests.fakes:build_publisher")


VALID = """
[receiver]
type = "fake"
label = "rx"

[selection]
channels = "channels.txt"

[checks]
max_removed = 3
min_guide_match = 0.8

[guides.freeview]
type = "fake"

[[publishers]]
type = "fake"
name = "one"
guide = "freeview"

[[publishers]]
type = "fake"
name = "two"
"""


def _load(tmp_path: Path, text: str, env=None):
    (tmp_path / "channels.txt").write_text("BBC One HD\n")
    path = tmp_path / "sat-to-ip.toml"
    path.write_text(text)
    return load_config(path, env or {})


def test_valid_config_builds_everything(tmp_path):
    config = _load(tmp_path, VALID)
    assert config.receiver.name == "rx"
    assert list(config.guides) == ["freeview"] and config.guides["freeview"].name == "freeview"
    assert [p.name for p in config.publishers] == ["one", "two"]
    assert config.checks.max_removed == 3 and config.checks.min_guide_match == 0.8
    assert config.channels_file == (tmp_path / "channels.txt").resolve()


def test_defaults(tmp_path):
    config = _load(tmp_path, '[receiver]\ntype = "fake"\n[[publishers]]\ntype = "fake"\n')
    assert config.checks.max_removed == 5 and config.checks.min_guide_match == 0.5
    assert config.channels_file is None and config.guides == {}
    assert config.publishers[0].name == "fake"


@pytest.mark.parametrize(
    "text, message",
    [
        ("not toml [", "invalid TOML"),
        ('[[publishers]]\ntype = "fake"\n', "missing [receiver]"),
        ('[receiver]\ntype = "fake"\n', "at least one [[publishers]]"),
        ('[receiver]\ntype = "nope"\n', "receiver.type: unknown receiver type 'nope' (known: fake, octopus_net)"),
        ('[receiver]\ntype = "fake"\nlable = "x"\n[[publishers]]\ntype = "fake"\n', "receiver: unknown key(s) lable"),
        ('[receiver]\ntype = "fake"\n[extra]\n[[publishers]]\ntype = "fake"\n', "unknown section(s) extra"),
        (
            '[receiver]\ntype = "fake"\n[checks]\nmax_removed = -1\n[[publishers]]\ntype = "fake"\n',
            "checks.max_removed: must be at least 0",
        ),
        (
            '[receiver]\ntype = "fake"\n[checks]\nmin_guide_match = 2\n[[publishers]]\ntype = "fake"\n',
            "checks.min_guide_match: must be at most 1",
        ),
        (
            '[receiver]\ntype = "fake"\n[checks]\nmax_removed = "5"\n[[publishers]]\ntype = "fake"\n',
            "must be an integer",
        ),
        (
            '[receiver]\ntype = "fake"\n[checks]\nmax_removed = true\n[[publishers]]\ntype = "fake"\n',
            "must be an integer",
        ),
        ('[receiver]\ntype = "fake"\nguides = 1\n[[publishers]]\ntype = "fake"\n', "receiver: unknown key(s) guides"),
        ('guides = 1\n[receiver]\ntype = "fake"\n[[publishers]]\ntype = "fake"\n', "guides: must be a table"),
        (
            '[receiver]\ntype = "fake"\n[[publishers]]\ntype = "fake"\nguide = "missing"\n',
            "publishers[0].guide: no guide source named 'missing'",
        ),
        (
            '[receiver]\ntype = "fake"\n[guides.g]\ntype = "fake"\nkind = "xmltv"\n[[publishers]]\ntype = "fake"\nguide = "g"\n',
            "can't use xmltv guides",
        ),
        (
            '[receiver]\ntype = "fake"\n[[publishers]]\ntype = "fake"\n[[publishers]]\ntype = "fake"\n',
            "'fake' is used twice",
        ),
        ('receiver = 3\n[[publishers]]\ntype = "fake"\n', "receiver: must be a table"),
    ],
)
def test_invalid_configs_name_the_problem(tmp_path, text, message):
    with pytest.raises(ConfigError) as exc:
        _load(tmp_path, text)
    assert message in str(exc.value)


def test_unreadable_config(tmp_path):
    with pytest.raises(ConfigError, match="Can't read config"):
        load_config(tmp_path / "missing.toml", {})


def _section(data, env=None, base=Path("/base")):
    return Section("x", data, base, env or {})


def test_section_getters():
    s = _section(
        {"s": "v", "i": 3, "f": 1, "b": True, "l": ["a"], "p": "sub/f.txt", "u": "http://h:1/", "e": "VAR"},
        env={"VAR": "secret"},
    )
    assert s.get_str("s") == "v" and s.get_int("i") == 3 and s.get_float("f") == 1.0 and s.get_bool("b") is True
    assert s.get_str_list("l") == ["a"] and s.get_path("p") == Path("/base/sub/f.txt")
    assert s.get_url("u") == "http://h:1" and s.get_secret("e") == "secret"
    assert s.get_optional_str("missing") is None and s.get_optional_path("missing") is None
    assert s.get_optional_path("p") == Path("/base/sub/f.txt")
    s.finish()


@pytest.mark.parametrize(
    "call, message",
    [
        (lambda s: s.get_str("missing"), "x.missing: required"),
        (lambda s: s.get_str("blank"), "x.blank: must not be empty"),
        (lambda s: s.get_int("i", minimum=5), "x.i: must be at least 5"),
        (lambda s: s.get_float("i", minimum=5), "x.i: must be at least 5"),
        (lambda s: s.get_str_list("badlist"), "list of non-empty strings"),
        (lambda s: s.get_url("s"), "must start with http://"),
        (lambda s: s.get_secret("missing_env"), "x.missing_env: required"),
        (lambda s: s.get_secret("e"), "environment variable UNSET is not set"),
        (lambda s: s.get_bool("s"), "must be true or false"),
    ],
)
def test_section_errors(call, message):
    s = _section({"blank": " ", "i": 3, "badlist": ["", 1], "s": "ftp://x", "e": "UNSET"})
    with pytest.raises(ConfigError, match=message.replace("(", r"\(")):
        call(s)


def test_optional_secret_may_be_absent():
    s = _section({"e": "UNSET"})
    assert s.get_secret("e", required=False) is None
    assert s.get_secret("other_env", required=False) is None


def test_unloadable_builder_is_a_config_error(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.BUILDERS["publisher"], "broken", "sat_to_ip.no_such_module:from_config")
    with pytest.raises(
        ConfigError, match="publishers\\[0\\].type: publisher type 'broken' is registered but can't be loaded"
    ):
        _load(tmp_path, '[receiver]\ntype = "fake"\n[[publishers]]\ntype = "broken"\n')


def test_missing_channels_file_is_a_config_error(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[receiver]\ntype = "fake"\n[selection]\nchannels = "nope.txt"\n[[publishers]]\ntype = "fake"\n')
    with pytest.raises(ConfigError, match="selection.channels: no such file"):
        load_config(path, {})


def test_publishers_may_not_share_an_output_dir(tmp_path, monkeypatch):
    from tests.fakes import FakePublisher

    def build(section):
        pub = FakePublisher(name=section.name, guide=None)
        pub.output_dir = section.get_path("output_dir")  # type: ignore[attr-defined]
        return pub

    monkeypatch.setattr("tests.fakes.build_publisher", build)
    text = '[receiver]\ntype = "fake"\n'
    text += '[[publishers]]\ntype = "fake"\nname = "a"\noutput_dir = "out"\n'
    text += '[[publishers]]\ntype = "fake"\nname = "b"\noutput_dir = "./out/"\n'
    with pytest.raises(ConfigError, match="publishers\\[1\\].output_dir: also used by 'a'"):
        _load(tmp_path, text)


def test_channel_list_is_parsed_at_load(tmp_path):
    config = _load(tmp_path, VALID)
    assert [spec.name for spec in config.channel_specs] == ["BBC One HD"]


def test_bad_channel_list_is_a_config_error(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[receiver]\ntype = "fake"\n[selection]\nchannels = "ch.txt"\n[[publishers]]\ntype = "fake"\n')
    (tmp_path / "ch.txt").write_text("5 = A\n3 = B\n")
    with pytest.raises(ConfigError, match="numbers must go up"):
        load_config(path, {})
