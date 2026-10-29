import runpy
from unittest.mock import MagicMock

import pytest
import requests

from sat_to_ip import cli
from sat_to_ip.exceptions import StepError


def _run_main(monkeypatch, argv, step):
    monkeypatch.setattr("sys.argv", ["sat-to-ip", *argv])
    monkeypatch.setattr(cli, "STEPS", dict.fromkeys(cli.ALL_STEPS, step))
    cli.main()


def test_main_runs_requested_steps_in_order(monkeypatch):
    steps = {n: MagicMock(name=f"step_{n}") for n in cli.ALL_STEPS}
    parent = MagicMock()
    for n, step in steps.items():
        parent.attach_mock(step, f"step_{n}")
    monkeypatch.setattr("sys.argv", ["sat-to-ip", "--steps", "3", "1"])
    monkeypatch.setattr(cli, "STEPS", steps)
    cli.main()
    assert [c[0] for c in parent.mock_calls] == ["step_1", "step_3"]


def test_main_runs_all_steps_by_default(monkeypatch):
    step = MagicMock()
    _run_main(monkeypatch, [], step)
    assert step.call_count == 8


@pytest.mark.parametrize(
    "error, message",
    [
        (StepError("boom"), "boom"),
        (TimeoutError("slow"), "--scan-timeout"),
        (requests.exceptions.ConnectionError("down"), "is the host reachable"),
        (requests.exceptions.HTTPError("500"), "HTTP error"),
        (KeyError("oops"), "unexpected error"),
    ],
)
def test_main_turns_errors_into_exit_messages(monkeypatch, error, message):
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["--steps", "1"], MagicMock(side_effect=error))
    assert message in str(exc.value)


def test_python_dash_m_runs_the_cli(monkeypatch):
    monkeypatch.setattr("sys.argv", ["sat_to_ip", "--help"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("sat_to_ip", run_name="__main__")
    assert exc.value.code == 0
