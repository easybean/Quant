from quant_data.massive_rate import wait_for_slot


def test_shared_persistent_spacing(tmp_path, monkeypatch):
    waits = []
    monkeypatch.setattr("quant_data.massive_rate.time.time", lambda: 1000.0)
    monkeypatch.setattr("quant_data.massive_rate.time.sleep", waits.append)
    wait_for_slot(tmp_path)
    wait_for_slot(tmp_path)
    assert waits == [16.0]


def test_invalid_timestamp_does_not_remove_throttle(tmp_path, monkeypatch):
    folder = tmp_path / "manifests"; folder.mkdir()
    (folder / "massive-last-request.txt").write_text("nan")
    waits = []
    monkeypatch.setattr("quant_data.massive_rate.time.time", lambda: 1000.0)
    monkeypatch.setattr("quant_data.massive_rate.time.sleep", waits.append)
    wait_for_slot(tmp_path)
    assert waits == [16.0]
