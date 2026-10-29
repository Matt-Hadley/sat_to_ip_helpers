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
    code = cli.main(
        ["-v", "run", "-c", str(config_file), "--stages", "publish", "--dry-run", "--allow-removals", "--interactive"]
    )
    assert code == 1
    assert seen["options"] == pipeline.RunOptions(
        stages=frozenset({pipeline.Stage.PUBLISH}), allow_removals=True, interactive=True, dry_run=True
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
