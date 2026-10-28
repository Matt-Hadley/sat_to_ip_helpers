import pytest

from utils_pipeline import csv_mappings
from utils_pipeline.csv_mappings import available_regions, load_csv_mappings, load_region_mappings


def test_load_csv_mappings(tmp_path):
    csv_file = tmp_path / "test.csv"
    with open(csv_file, "w", encoding="utf-8") as f:
        f.write("Name,Callsign\n")
        f.write("BBC One HD,BBC1HD.gb\n")
        f.write("ITV HD,ITV1HD.gb\n")

    mappings = load_csv_mappings(str(csv_file))
    assert mappings == {
        "BBC One HD": "BBC1HD.gb",
        "ITV HD": "ITV1HD.gb",
    }


def test_load_csv_mappings_invalid_headers(tmp_path):
    csv_file = tmp_path / "test.csv"
    with open(csv_file, "w", encoding="utf-8") as f:
        f.write("Wrong,Headers\n")
        f.write("A,B\n")

    mappings = load_csv_mappings(str(csv_file))
    assert mappings == {}


def test_load_csv_mappings_missing_file():
    # Should raise FileNotFoundError (standard open() behavior)
    try:
        load_csv_mappings("/non/existent/file.csv")
        raise AssertionError("Should have raised FileNotFoundError")
    except FileNotFoundError:
        pass


def test_available_regions_includes_builtin_uk():
    assert "uk" in available_regions()


def test_available_regions_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(csv_mappings, "_BUILTIN_MAPPINGS_DIR", tmp_path / "missing")
    assert available_regions() == []


def test_load_region_mappings_merges_csv_files(tmp_path, monkeypatch):
    region = tmp_path / "xx"
    region.mkdir()
    (region / "a.csv").write_text("Name,Callsign\nOne,ONE\nTwo,OLD\n", encoding="utf-8")
    (region / "b.csv").write_text("Name,Callsign\nTwo,TWO\n", encoding="utf-8")
    monkeypatch.setattr(csv_mappings, "_BUILTIN_MAPPINGS_DIR", tmp_path)
    assert load_region_mappings("XX") == {"One": "ONE", "Two": "TWO"}


def test_load_region_mappings_unknown_region():
    with pytest.raises(ValueError, match="Unknown mapping region"):
        load_region_mappings("nowhere")


def test_load_csv_mappings_skips_short_rows(tmp_path):
    csv_file = tmp_path / "test.csv"
    csv_file.write_text("Name,Callsign\nOnly\n A , B \n", encoding="utf-8")
    assert load_csv_mappings(str(csv_file)) == {"A": "B"}
