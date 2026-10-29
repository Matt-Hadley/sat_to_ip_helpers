from sat_to_ip.kingofsat.transponder import Transponder
from sat_to_ip.octopus_net.transponders import build_upload_payload, format_source


def _make_transponder(**kwargs) -> Transponder:
    defaults = {
        "satellite": "Astra 2E",
        "frequency": 10773.0,
        "polarization": "H",
        "system": "DVB-S2",
        "modulation": "8PSK",
        "symbol_rate": 23000,
    }
    defaults.update(kwargs)
    return Transponder(**defaults)


# ---------------------------------------------------------------------------
# format_source
# ---------------------------------------------------------------------------


def test_format_source_key_strips_dot_and_degree():
    t = _make_transponder()
    source = format_source([t], "28.2E", "Astra 2E")
    assert source["Key"] == "282E"


def test_format_source_title():
    t = _make_transponder()
    source = format_source([t], "28.2E", "Astra 2E")
    assert source["Title"] == "28.2E - Astra 2E"


def test_format_source_dvb_type_is_satellite():
    t = _make_transponder()
    source = format_source([t], "28.2E", "Astra 2E")
    assert source["DVBType"] == "S"


def test_format_source_request_string_format():
    t = _make_transponder(frequency=10773.0, polarization="H", modulation="8PSK", system="DVB-S2", symbol_rate=23000)
    source = format_source([t], "28.2E", "Astra 2E")
    request = source["TransponderList"][0]["Request"]
    assert request == "freq=10773&pol=h&mtype=8PSK&msys=dvbs2&sr=23000"


def test_format_source_dvbs_system_lowercased():
    t = _make_transponder(system="DVB-S")
    source = format_source([t], "28.2E", "Astra 2E")
    request = source["TransponderList"][0]["Request"]
    assert "msys=dvbs&" in request


def test_format_source_multiple_transponders():
    transponders = [_make_transponder(frequency=f) for f in [10773.0, 10847.0, 10936.0]]
    source = format_source(transponders, "28.2E", "Astra 2E")
    assert len(source["TransponderList"]) == 3


def test_format_source_sends_the_exact_frequency_never_rounded():
    requests = [
        format_source([_make_transponder(frequency=f)], "28.2E", "Astra 2E")["TransponderList"][0]["Request"]
        for f in (10773.0, 10773.25, 10935.5, 11111.75, 12031.4)
    ]
    assert [r.split("&")[0] for r in requests] == [
        "freq=10773",
        "freq=10773.25",
        "freq=10935.5",
        "freq=11111.75",
        "freq=12031.4",
    ]


# ---------------------------------------------------------------------------
# build_upload_payload
# ---------------------------------------------------------------------------


def test_build_upload_payload_wraps_sources():
    sources = [{"Key": "282E"}, {"Key": "130E"}]
    payload = build_upload_payload(sources)
    assert payload["SourceList"] == sources
    assert payload["GroupList"] == []


def test_build_upload_payload_empty_sources():
    payload = build_upload_payload([])
    assert payload == {"GroupList": [], "SourceList": []}
