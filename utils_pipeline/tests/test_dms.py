from utils_pipeline.dms import build_dms_entries


def test_build_dms_entries():
    dms = [{"name": "BBC One HD", "type": "video"}]
    available = [{"name": "Radio 1", "type": "audio"}]
    channels, origins, selected = build_dms_entries(dms, available)

    # Sorted by video-first then name
    assert channels[0]["name"] == "BBC One HD"
    assert origins[0] == "DMS"
    assert selected[0] is True

    assert channels[1]["name"] == "Radio 1"
    assert origins[1] == "available"
    assert selected[1] is False
