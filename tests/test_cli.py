import runpy

import pytest

from sat_to_ip import cli, pipeline, registry


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.BUILDERS["receiver"], "fake", "tests.fakes:build_receiver")
    monkeypatch.setitem(registry.BUILDERS["guide"], "fake", "tests.fakes:build_guide")
    monkeypatch.setitem(registry.BUILDERS["publisher"], "fake", "tests.fakes:build_publisher")
    path = tmp_path / "c.toml"
    path.write_text(
        '[receiver]\ntype = "fake"\n[guides.g]\ntype = "fake"\n[[publishers]]\ntype = "fake"\nguide = "g"\n'
    )
    return path


def test_check_config_ok(config_file, capsys):
    assert cli.main(["check-config", "-c", str(config_file)]) == 0
    out = capsys.readouterr().out
    assert "receiver fake; guides: g (fake)" in out and "Publishers: fake" in out


def test_config_error_exits_2(tmp_path):
    assert cli.main(["run", "-c", str(tmp_path / "missing.toml")]) == 2


def test_run_passes_options_and_returns_report_code(config_file, monkeypatch):
    seen = {}

    def fake_run(config, options):
        seen["options"] = options
        return pipeline.RunReport([pipeline.Outcome("publish:x", "failed")])

    monkeypatch.setattr(pipeline, "run", fake_run)
    code = cli.main(["-v", "run", "-c", str(config_file), "--stages", "publish", "--dry-run", "--allow-removals"])
    assert code == 1
    assert seen["options"] == pipeline.RunOptions(
        stages=frozenset({pipeline.Stage.PUBLISH}), allow_removals=True, dry_run=True
    )


def test_run_end_to_end_with_fakes(config_file):
    assert cli.main(["run", "-c", str(config_file)]) == 0


def test_command_is_required(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_python_dash_m(monkeypatch, config_file):
    monkeypatch.setattr("sys.argv", ["sat_to_ip", "check-config", "-c", str(config_file)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("sat_to_ip", run_name="__main__")
    assert exc.value.code == 0


def test_urllib3_retry_noise_is_hidden_unless_verbose(config_file):
    import logging

    cli.main(["check-config", "-c", str(config_file)])
    assert logging.getLogger("urllib3").level == logging.ERROR
    logging.getLogger("urllib3").setLevel(logging.NOTSET)
    cli.main(["-v", "check-config", "-c", str(config_file)])
    assert logging.getLogger("urllib3").level == logging.NOTSET


def test_config_error_is_one_plain_line_on_stderr(tmp_path, capsys):
    assert cli.main(["check-config", "-c", str(tmp_path / "missing.toml")]) == 2
    err = capsys.readouterr().err
    assert err.startswith("Config error: Can't read config") and err.count("\n") == 1


def test_check_config_notes_missing_channels_and_folders(tmp_path, monkeypatch, capsys):
    from tests.fakes import FakePublisher

    def build(section):
        pub = FakePublisher(name=section.name, guide=None)
        pub.output_dir = section.get_path("output_dir")  # type: ignore[attr-defined]
        return pub

    monkeypatch.setitem(registry.BUILDERS["receiver"], "fake", "tests.fakes:build_receiver")
    monkeypatch.setitem(registry.BUILDERS["publisher"], "fake", "tests.fakes:build_publisher")
    monkeypatch.setattr("tests.fakes.build_publisher", build)
    (tmp_path / "exists").mkdir()
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    path = tmp_path / "c.toml"
    path.write_text(
        '[receiver]\ntype = "fake"\n'
        '[[publishers]]\ntype = "fake"\nname = "a"\noutput_dir = "exists"\n'
        '[[publishers]]\ntype = "fake"\nname = "b"\noutput_dir = "new/sub"\n'
        '[[publishers]]\ntype = "fake"\nname = "c"\noutput_dir = "locked/x"\n'
    )
    assert cli.main(["check-config", "-c", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Note: no [[channels]] yet; pick them with: sat-to-ip channels" in out
    assert "Note: b: " in out and "doesn't exist yet; it'll be created" in out
    assert "Note: c: can't create or write" in out
    assert "Note: a:" not in out
    locked.chmod(0o700)
