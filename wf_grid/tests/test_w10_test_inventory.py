from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def test_volume_filter_inventory_artifact_exists_and_lists_required_commands():
    inventory = ROOT / "docs" / "volume_filter_test_inventory.md"

    text = inventory.read_text(encoding="utf-8")

    assert "TradeFilterZigZagConfig\\(" in text
    assert "validate_trade_filter|_validate_trade_filter|trade_filter\\.enabled" in text
    assert "zigzag_st_filter\\.apply|from.*zigzag_st_filter.*apply" in text
    assert "NEW_VOLUME" in text
    assert "PRESERVED" in text
    assert "UPDATE" in text


def test_pytest_discovery_contract_keeps_required_paths_and_slow_perf_marker():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pytest_options = pyproject["tool"]["pytest"]["ini_options"]

    assert pytest_options["testpaths"] == ["wf_grid/tests", "donor TESTER/tests"]
    assert "slow_perf: marks volume-filter performance smoke tests" in pytest_options["markers"]
