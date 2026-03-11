from utils_pipeline.scan import compute_scan_diff, restore_dms_after_scan

def test_compute_scan_diff():
    pre = [
        {"name": "A", "serviceid": "1", "frequency": 10773},
        {"name": "B", "serviceid": "2", "frequency": 11386},
    ]
    post = [
        {"name": "A", "serviceid": "1", "frequency": 10773}, # kept
        {"name": "C", "serviceid": "3", "frequency": 11386}, # new
    ]
    diff = compute_scan_diff(pre, post)
    assert len(diff.added) == 1
    assert diff.added[0]["name"] == "C"
    assert len(diff.removed) == 1
    assert diff.removed[0]["name"] == "B"

def test_restore_dms_after_scan():
    pre_dms = [{"name": "A", "serviceid": "1", "frequency": 10773, "position": 0}]
    post_available = [{"name": "A", "serviceid": "1", "frequency": 10773}]
    
    restored, missing = restore_dms_after_scan(pre_dms, post_available)
    # restored channel should have position copied from pre_dms
    assert restored == [{"name": "A", "serviceid": "1", "frequency": 10773, "position": 0}]
    assert missing == []

def test_restore_dms_after_scan_missing():
    pre_dms = [{"name": "A", "serviceid": "1", "frequency": 10773}]
    post_available = []
    
    restored, missing = restore_dms_after_scan(pre_dms, post_available)
    assert restored == []
    assert missing == ["A"]
