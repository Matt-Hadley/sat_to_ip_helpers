from utils_pipeline.dms import build_dms_entries, merge_dms_selections


def test_merge_dms_selections():
    dms = [{"name": "A", "serviceid": "1", "position": 0}]
    chosen = [
        {"name": "A", "serviceid": "1"},  # existing
        {"name": "B", "serviceid": "2"},  # new
    ]
    final = merge_dms_selections(dms, chosen)
    assert len(final) == 2
    assert final[0]["name"] == "A"
    assert final[0]["position"] == 0
    assert final[1]["name"] == "B"
    assert final[1]["position"] == 1


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
