import pytest

from quant_data.data_source_drafts import DataSourceDraftInputError, DataSourceDraftStore


def _source(**changes):
    data = {"name": "美股声明", "provider": "alpaca", "market": "US", "product": "US_EQUITY", "frequency": "1d", "coverage_declaration": "待确认美股日线覆盖。", "license_declaration": "待确认研究用途许可。", "credential_reference": "ALPACA_DATA_REF", "verification_status": "declared"}
    data.update(changes)
    return data


def test_saves_immutable_declared_versions_only(tmp_path):
    store = DataSourceDraftStore(tmp_path); store.initialize()
    first = store.save(_source())
    second = store.save(_source(name="美股声明 v2", frequency="1h"), first["id"])
    assert second["version"] == 2
    assert second["source"]["verification_status"] == "declared"
    assert [item["version"] for item in store.history(first["id"])] == [2, 1]
    assert "secret" not in second["source"]


@pytest.mark.parametrize("changes, message", [
    ({"provider": "unknown"}, "approved"), ({"product": "CRYPTO_SPOT", "market": "CRYPTO"}, "does not support"),
    ({"frequency": "tick"}, "frequency"), ({"credential_reference": "/tmp/key"}, "reference"),
    ({"verification_status": "verified"}, "fixed to declared"), ({"secret": "nope"}, "only declared"),
])
def test_rejects_unknown_combinations_credential_values_and_verification_claims(tmp_path, changes, message):
    store = DataSourceDraftStore(tmp_path); store.initialize()
    with pytest.raises(DataSourceDraftInputError, match=message): store.save(_source(**changes))
    assert store.list() == []
