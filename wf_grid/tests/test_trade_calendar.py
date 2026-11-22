import hashlib
from pathlib import Path

import pandas as pd
import pytest
import yaml

from wf_grid.wf.trade_calendar import load_trade_calendar
from wf_grid.config.loader import ConfigError, load_grid_config
from supertrend_optimizer.core.trade_filter_config import resolve_trade_filter_mode



INDEX = pd.to_datetime(["2024-02-29 23:45", "2024-03-01 00:00", "2024-03-04 09:00"])


def write_profile(tmp_path, block):
    root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    raw["trade_filter"]["time_filter"]["enabled"] = False
    raw["trade_filter"]["date_filter"] = block
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_date_only_config(tmp_path):
    cfg = load_grid_config(str(write_profile(tmp_path, {"enabled": True})))
    assert resolve_trade_filter_mode(cfg.trade_filter) == "date_only"
    assert cfg.trade_filter.date_filter.file_path == "data 1.csv"


@pytest.mark.parametrize("block", [None, [], "yes", {}, {"enabled": False},
                                  {"enabled": 1}, {"file_path": " "},
                                  {"enabled": True, "other": 1}])
def test_invalid_config(tmp_path, block):
    with pytest.raises(ConfigError):
        load_grid_config(str(write_profile(tmp_path, block)))


@pytest.mark.parametrize("contents", [
    '<DATE>\n\n"20240304"\n20240229\n20240229\n',
    '\ufefftext;<DATE>\n"a;b";20240229\n"line\nbreak";\t20240304 \n',
])
def test_membership_and_fingerprint(tmp_path, contents):
    path = tmp_path / "list.csv"
    path.write_bytes(contents.encode())
    result = load_trade_calendar(str(path), INDEX)
    assert result.allowed.tolist() == [True, False, True]
    assert result.boundary.tolist() == [True, False, False]
    assert result.entry.tolist() == [False, False, True]
    assert result.metadata["sha256"] == hashlib.sha256(b"20240229\n20240304").hexdigest()


@pytest.mark.parametrize("contents,error", [
    (b"", "header"), (b"<DATE>\n", "empty date list"), (b"<date>\n", "line 1"),
    (b"<DATE>;<DATE>\n", "line 1"), (b"<DATE>\n\"\"\n", "line 2"),
    (b"x;<DATE>\nx\n", "line 2"), (b"<DATE>\n20240229\n20230229", "line 3"),
    (b"<DATE>\n2024011", "line 2"), (b"<DATE>\n\"20240229", "line 2"),
    (b'<DATE>;text\n20240229;a"b', "unexpected quote on line 2"),
    (b"<DATE>\n\xff", "codec"), (b"<DATE>\n19990101", "list=1, history=3"),
])
def test_invalid_calendar(tmp_path, contents, error):
    path = tmp_path / "list.csv"
    path.write_bytes(contents)
    with pytest.raises(ValueError, match=error) as caught:
        load_trade_calendar(str(path), INDEX)
    assert str(path) in str(caught.value)


def test_reference_data_from_another_cwd(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(tmp_path)
    bars = pd.read_csv(root / "data.csv", sep=";", usecols=["<DATE>", "<TIME>"], dtype=str)
    index = pd.DatetimeIndex(pd.to_datetime(
        bars["<DATE>"] + bars["<TIME>"], format="%Y%m%d%H%M%S"
    ))
    result = load_trade_calendar("data 1.csv", index)
    assert (len(index), index.normalize().nunique()) == (190620, 1112)
    assert result.metadata["unique_dates"] == result.metadata["matched_dates"] == 820
    assert result.metadata["source_rows"] == 147439
    assert result.allowed.sum() == 147439
    assert result.boundary.sum() == 157
    assert set(index[result.boundary].strftime("%H:%M")) == {"23:45"}
