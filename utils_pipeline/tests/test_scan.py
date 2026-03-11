from utils_pipeline.scan import compute_scan_diff


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
