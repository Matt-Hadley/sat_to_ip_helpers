import logging

from sat_to_ip.octopus_net.channels import log_scan_diff


def test_log_scan_diff_no_changes(caplog):
    caplog.set_level(logging.INFO)
    log_scan_diff([{"serviceid": "1"}], [{"serviceid": "1"}])
    assert "No changes detected" in caplog.text


def test_log_scan_diff_logs_every_kind_of_change(caplog):
    caplog.set_level(logging.INFO)
    log_scan_diff(
        [{"name": "Kept", "serviceid": "1"}, {"name": "Gone", "serviceid": "2"}],
        [{"name": "Kept", "serviceid": "1"}, {"name": "New", "serviceid": "3"}],
    )
    assert "+ New" in caplog.text and "- Gone" in caplog.text and "Kept" not in caplog.text
