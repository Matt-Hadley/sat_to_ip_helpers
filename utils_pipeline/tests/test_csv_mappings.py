import csv
import os
from utils_pipeline.csv_mappings import load_csv_mappings

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
        assert False, "Should have raised FileNotFoundError"
    except FileNotFoundError:
        pass
