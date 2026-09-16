import sqlite3

import pytest

from quant_data.paper_ledger import PaperLedgerConflictError, PaperLedgerInputError, PaperLedgerStore


def _account(**changes):
    body = {"name": "研究模拟账户", "base_currency": "USD", "initial_cash": "1000", "margin_mode": "cash"}
    body.update(changes)
    return body


def _event(**changes):
    body = {"event_type": "fill", "dedupe_key": "fill-001", "event": {"order_id": "order-001", "symbol": "SPY", "price": "100"}, "cash_delta": "-400", "position_delta": "4"}
    body.update(changes)
    return body


def test_paper_account_and_immutable_deduplicated_ledger_reconcile_exactly(tmp_path):
    store = PaperLedgerStore(tmp_path)
    store.initialize()
    account = store.create_account(_account())
    fill = store.append_event(account["id"], _event())
    assert store.append_event(account["id"], _event()) == fill
    fee = store.append_event(account["id"], _event(event_type="fee", dedupe_key="fee-001", event={"fill_id": fill["id"], "amount": "1"}, cash_delta="-1", position_delta="0"))
    audit = store.append_event(account["id"], _event(event_type="audit", dedupe_key="audit-001", event={"action": "manual-review"}, cash_delta="0", position_delta="0"))
    assert [event["event_type"] for event in store.events(account["id"])] == [audit["event_type"], fee["event_type"], fill["event_type"]]
    reconciliation = store.reconciliation(account["id"])
    assert reconciliation["reconciled_cash"] == "599"
    assert reconciliation["net_recorded_position"] == "4"
    assert "not PnL" in reconciliation["scope"]
    with pytest.raises(PaperLedgerConflictError):
        store.append_event(account["id"], _event(cash_delta="-401"))
    with sqlite3.connect(store.db_path) as db:
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            db.execute("DELETE FROM paper_ledger_events WHERE id=?", (fill["id"],))


@pytest.mark.parametrize("payload, message", [
    (_account(initial_cash="0"), "positive"),
    (_account(base_currency="usd"), "uppercase"),
    (_event(event_type="order", cash_delta="1"), "cannot move"),
    (_event(event_type="fee", cash_delta="1", position_delta="0"), "negative"),
    (_event(event_type="fill", position_delta="0"), "non-zero"),
])
def test_paper_ledger_rejects_unaccountable_inputs(tmp_path, payload, message):
    store = PaperLedgerStore(tmp_path)
    store.initialize()
    if "base_currency" in payload:
        with pytest.raises(PaperLedgerInputError, match=message):
            store.create_account(payload)
    else:
        account = store.create_account(_account())
        with pytest.raises(PaperLedgerInputError, match=message):
            store.append_event(account["id"], payload)
