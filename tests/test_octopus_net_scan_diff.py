import logging

from sat_to_ip.octopus_net.channels import compute_scan_diff, log_scan_diff


def test_compute_scan_diff():
    pre = [
        {"name": "A", "serviceid": "1", "frequency": 10773},
        {"name": "B", "serviceid": "2", "frequency": 11386},
    ]
    post = [
        {"name": "A", "serviceid": "1", "frequency": 10773},  # kept
        {"name": "C", "serviceid": "3", "frequency": 11386},  # new
    ]
    diff = compute_scan_diff(pre, post)
    assert len(diff.added) == 1
    assert diff.added[0]["name"] == "C"
    assert len(diff.removed) == 1
    assert diff.removed[0]["name"] == "B"


def test_compute_scan_diff_detects_frequency_change():
    diff = compute_scan_diff(
        [{"name": "A", "serviceid": "1", "frequency": 10773}],
        [{"name": "A", "serviceid": "1", "freq": 11386}],
    )
    assert diff.has_changes
    assert [(b["frequency"], a["freq"]) for b, a in diff.freq_changed] == [(10773, 11386)]


def test_log_scan_diff_no_changes(caplog):
    caplog.set_level(logging.INFO)
    log_scan_diff([{"serviceid": "1"}], [{"serviceid": "1"}])
    assert "No changes detected" in caplog.text


def test_log_scan_diff_logs_every_kind_of_change(caplog):
    caplog.set_level(logging.INFO)
    log_scan_diff(
        [{"name": "Gone", "serviceid": "1"}, {"name": "Moved", "serviceid": "2", "frequency": 1}],
        [{"name": "New", "serviceid": "3"}, {"name": "Moved", "serviceid": "2", "frequency": 2}],
    )
    assert "+ New" in caplog.text
    assert "- Gone" in caplog.text
    assert "~ Moved  1 → 2" in caplog.text
