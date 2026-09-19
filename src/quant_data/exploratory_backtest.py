"""Offline, fixed-scope TSLA exploratory daily-bar replay.

This module is deliberately separate from the product backtest surface.  It
accepts one frozen, local snapshot layout and always reports that the input is
not PIT-qualified and cannot enable a formal backtest.  It has no network,
job, API, UI, broker, or data-discovery entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
import exchange_calendars as calendars

from .accounting import AccountSnapshot, apply_cash_fills
from .daily_execution import CostModel, DailyBar, DailyExecutionResult, Order, execute_daily_order


UTC = timezone.utc
SCHEMA_VERSION = "exploratory-tsla-daily-backtest-v1"
SNAPSHOT_SCHEMA = "exploratory-tsla-daily-snapshot-v1"
SYMBOL = "TSLA"
START = date(2026, 1, 2)
END = date(2026, 8, 31)
EXPECTED_ROWS = 166
SOURCE = "nasdaq_web_unadjusted"
PRICE_BASIS = "unadjusted"
SOURCE_RELATIVE_PATH = "bars/daily/symbol=TSLA-2ff6985a/bars.parquet"
SOURCE_SHA256 = "2a5f4299bd39622d55df21a044de58844993a0ef58bba8559c07b63487713c6e"
INITIAL_CASH = Decimal("10000")
LIMIT_BUFFER = Decimal("0.10")
COST = CostModel(
    per_share_commission=Decimal("0.01"),
    minimum_commission=Decimal("1"),
    # CostModel interprets spread_bps as full quoted spread, then applies half.
    spread_bps=Decimal("5"),
    slippage_bps=Decimal("5"),
    max_participation=Decimal("0.01"),
)
WARNING = "exploratory_no_PIT_qualification"


class ExploratoryBacktestRejected(ValueError):
    """The frozen input is malformed, changed, or outside this replay scope."""


@dataclass(frozen=True)
class SnapshotBar:
    session: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    open_time: datetime
    close_time: datetime

    def execution_bar(self) -> DailyBar:
        return DailyBar(
            self.session, self.open_time, self.close_time,
            self.open, self.high, self.low, self.close, self.volume,
        )


def run(snapshot_dir: Path, data_root: Path | None = None) -> dict[str, Any]:
    """Replay exactly the frozen TSLA snapshot and return a pure JSON-safe report.

    ``data_root`` is optional and exists solely for an additional source-file
    hash check.  It is never used to discover, load, or supplement bars.
    """
    root = _snapshot_directory(snapshot_dir)
    manifest = _read_json(root / "manifest.json", "manifest.json")
    report = _read_json(root / "report.json", "report.json")
    raw_bars = _read_json(root / "bars.json", "bars.json")
    if not isinstance(manifest, Mapping) or not isinstance(report, Mapping):
        raise ExploratoryBacktestRejected("manifest.json and report.json must contain JSON objects")
    if not isinstance(raw_bars, list):
        raise ExploratoryBacktestRejected("bars.json must contain a JSON list")
    _verify_snapshot_files(root, manifest)
    _verify_manifest(manifest, data_root)
    bars = _parse_bars(raw_bars)
    _verify_snapshot_report(report, manifest)

    shared = {
        "symbol": SYMBOL,
        "date_range": {"start": START.isoformat(), "end": END.isoformat()},
        "initial_cash_usd": str(INITIAL_CASH),
        "currency": "USD",
        "price_basis": PRICE_BASIS,
        "corporate_actions": "blocked_if_any_marker_is_nonzero",
        "formal_backtest_enabled": False,
        "qualified": False,
        "warning": WARNING,
        "warnings": [WARNING],
        "provenance": {
            "snapshot_schema": SNAPSHOT_SCHEMA,
            "manifest_sha256": _sha256_file(root / "manifest.json"),
            "bars_sha256": _sha256_file(root / "bars.json"),
            "snapshot_report_sha256": _sha256_file(root / "report.json"),
            "source_relative_path": SOURCE_RELATIVE_PATH,
            "source_sha256": manifest["source_sha256"],
            "source": SOURCE,
            "calendar_version": _calendar_version(report),
            "observation_caveat": _observation_caveat(report),
        },
        "execution_contract": {
            "signal_timing": "session close signal -> next XNYS session open attempt",
            "order_type": "DAY limit only",
            "limit_rule": "prior close +/- 10%; limit is set before next open and never from future open",
            "gap_rule": "if next raw open breaches the pre-set limit, reject rather than infer an intraday recovery fill",
            "positioning": "USD long-only, whole shares, no leverage or financing",
            "max_participation": "0.01",
            "commission": {"per_share_usd": "0.01", "minimum_usd": "1"},
            "costs": {
                "slippage_bps": "5",
                "quoted_spread_bps": "5",
                "half_spread_bps": "2.5",
                "total_adverse_price_bps": "7.5",
            },
            "market_data_limitations": [
                "daily OHLCV does not establish intraday path, queue priority, or fill certainty",
                "observed_at is a later acquisition fact, not historical availability",
            ],
        },
        "fixed_priors": {
            "parameter_selection": "fixed before replay; no optimisation or parameter search",
            "buy_and_hold": "first eligible close signal, then full long target using the same next-session DAY-limit and cost contract",
            "sma_10_30": {"fast_window_sessions": 10, "slow_window_sessions": 30, "rule": "fast > slow => 100% long; otherwise 0%"},
        },
        "corporate_action_status": (
            "the freezer rejected nonzero source action columns; the report remains insufficient "
            "to prove complete corporate-action coverage, so no total-return claim is made"
        ),
        "comparison_caveat": (
            "buy_and_hold is signalled on the first close and SMA(10,30) has a 30-session warmup; "
            "their same-calendar-window returns have different exposure starts and no parameter optimisation occurred"
        ),
    }
    strategies = {
        "buy_and_hold": _run_strategy("buy_and_hold", bars),
        "sma_10_30": _run_strategy("sma_10_30", bars),
    }
    output = {"schema_version": SCHEMA_VERSION, "status": "succeeded_exploratory_only", **shared, "strategies": strategies}
    output["report_hash"] = _report_hash(output)
    return output


def _run_strategy(name: str, bars: Sequence[SnapshotBar]) -> dict[str, Any]:
    account = AccountSnapshot("USD", INITIAL_CASH)
    pending: tuple[dict[str, Any], Order] | None = None
    decisions: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    peak = INITIAL_CASH
    max_drawdown = Decimal("0")

    for index, bar in enumerate(bars):
        execution_bar = bar.execution_bar()
        if pending is not None:
            order_record, order = pending
            result = _execute_next_open_only(account, execution_bar, order)
            account = result.account
            _record_execution(order_record, result, bar)
            if result.cash_fill is not None:
                fill = result.cash_fill
                fills.append({
                    "order_id": order_record["order_id"], "execution_session": bar.session.isoformat(),
                    "side": "buy" if fill.quantity > 0 else "sell", "quantity": str(abs(fill.quantity)),
                    "signed_quantity": str(fill.quantity), "base_price": str(result.base_price),
                    "fill_price": str(fill.price), "spread_cost_per_share": str(result.spread_cost_per_share),
                    "slippage_cost_per_share": str(result.slippage_cost_per_share),
                    "spread_cost_usd": str(result.spread_cost_per_share * abs(fill.quantity)),
                    "slippage_cost_usd": str(result.slippage_cost_per_share * abs(fill.quantity)),
                    "commission_usd": str(fill.fee),
                })
            pending = None
        else:
            account = apply_cash_fills(account, (), mark_price=bar.close)

        target, detail = _target_for(name, bars, index, account)
        decision = {
            "session_date": bar.session.isoformat(), "scheduled_close_proxy_time": bar.close_time.isoformat(),
            "signal_source": "same_session_raw_close", "target_weight": str(target),
            "historical_availability": "unknown; scheduled-close timing is an exploratory replay assumption", **detail,
        }
        decisions.append(decision)
        if index + 1 < len(bars):
            next_bar = bars[index + 1]
            sizing_equity = account.free_cash + account.position_quantity * bar.close
            desired = _desired_position(name, target, sizing_equity, bar.close, account.position_quantity)
            delta = desired - account.position_quantity
            decision["next_session"] = next_bar.session.isoformat()
            decision["sizing_equity_usd"] = str(sizing_equity)
            if delta:
                side = "buy" if delta > 0 else "sell"
                limit = bar.close * (Decimal("1") + LIMIT_BUFFER if side == "buy" else Decimal("1") - LIMIT_BUFFER)
                order = Order(side, abs(delta), limit, bar.close_time)
                record = {
                    "order_id": f"{name}-{index + 1}", "strategy": name,
                    "signal_session": bar.session.isoformat(), "execution_session": next_bar.session.isoformat(),
                    "submitted_at": bar.close_time.isoformat(), "side": side, "quantity": str(abs(delta)),
                    "limit_price": str(limit), "time_in_force": "DAY", "target_weight": str(target),
                    "sizing_close": str(bar.close), "sizing_equity_usd": str(sizing_equity),
                    "status": "scheduled", "execution_reason": None,
                }
                orders.append(record)
                pending = (record, order)
            else:
                decision["order_status"] = "no_rebalance_required"
        else:
            decision["order_status"] = "not_submitted_no_next_session"
        equity = account.free_cash + account.position_quantity * bar.close
        _assert_reconciled(account, equity)
        peak = max(peak, equity)
        drawdown = equity / peak - Decimal("1")
        max_drawdown = min(max_drawdown, drawdown)
        equity_curve.append({
            "session_date": bar.session.isoformat(), "free_cash_usd": str(account.free_cash),
            "position_shares": str(account.position_quantity), "mark_price": str(bar.close),
            "equity_usd": str(equity), "nav": str(equity / INITIAL_CASH),
            "realized_pnl_usd": str(account.realized_pnl), "unrealized_pnl_usd": str(account.unrealized_pnl),
            "fees_usd": str(account.fees), "drawdown_pct": str(drawdown * Decimal("100")),
        })

    terminal = account.free_cash + account.position_quantity * bars[-1].close
    _assert_reconciled(account, terminal)
    spread_cost = sum((Decimal(fill["spread_cost_usd"]) for fill in fills), Decimal("0"))
    slippage_cost = sum((Decimal(fill["slippage_cost_usd"]) for fill in fills), Decimal("0"))
    return {
        "strategy": name,
        "formal_backtest_enabled": False,
        "qualified": False,
        "warning": WARNING,
        "decisions": decisions,
        "orders": orders,
        "fills": fills,
        "equity_curve": equity_curve,
        "ledger": _ledger(account, bars[-1].close, terminal),
        "metrics": {
            "initial_cash_usd": str(INITIAL_CASH), "terminal_equity_usd": str(terminal),
            "total_pnl_usd": str(terminal - INITIAL_CASH),
            "total_return_pct": str((terminal / INITIAL_CASH - Decimal("1")) * Decimal("100")),
            "max_drawdown_pct": str(max_drawdown * Decimal("100")), "order_count": len(orders),
            "fill_count": len(fills), "trade_count": len(fills), "commission_usd": str(account.fees),
            "estimated_half_spread_cost_usd": str(spread_cost), "estimated_slippage_cost_usd": str(slippage_cost),
        },
    }


def _target_for(name: str, bars: Sequence[SnapshotBar], index: int, account: AccountSnapshot) -> tuple[Decimal, dict[str, Any]]:
    if name == "buy_and_hold":
        return Decimal("1"), {"rule": "first close target full investment; retry after an unfilled DAY order, then hold actual position"}
    if name != "sma_10_30":
        raise AssertionError(f"unsupported fixed strategy {name}")
    closes = [bar.close for bar in bars[:index + 1]]
    fast = _sma(closes, 10)
    slow = _sma(closes, 30)
    target = Decimal("1") if fast is not None and slow is not None and fast > slow else Decimal("0")
    return target, {
        "rule": "SMA(10) > SMA(30) => full long; otherwise cash",
        "sma_10": None if fast is None else str(fast), "sma_30": None if slow is None else str(slow),
    }


def _desired_position(name: str, target: Decimal, equity: Decimal, close: Decimal, current: Decimal) -> Decimal:
    if target == 0:
        return Decimal("0")
    if name == "buy_and_hold" and current:
        return current
    return (equity * target / close).to_integral_value(rounding=ROUND_FLOOR)


def _execute_next_open_only(snapshot: AccountSnapshot, bar: DailyBar, order: Order) -> DailyExecutionResult:
    # `execute_daily_order` would conservatively permit a later daily limit
    # touch after a gap.  This replay explicitly forbids that approximation:
    # it is an opening attempt, capped by a pre-set marketable limit.
    gap_exceeds = (order.side == "buy" and bar.open > order.limit_price) or (
        order.side == "sell" and bar.open < order.limit_price
    )
    if gap_exceeds:
        marked = apply_cash_fills(snapshot, (), mark_price=bar.close)
        return DailyExecutionResult(
            "unfilled", "gap_exceeds_pre_set_limit", order.quantity, Decimal("0"), order.quantity,
            None, None, None, None, Decimal("0"), None, marked, (), (),
        )
    return execute_daily_order(snapshot, bar, order, COST)


def _record_execution(record: dict[str, Any], result: DailyExecutionResult, bar: SnapshotBar) -> None:
    record.update({
        "status": result.status, "execution_reason": result.reason,
        "filled_quantity": str(result.filled_quantity), "cancelled_quantity": str(result.remaining_quantity),
        "raw_open": str(bar.open), "base_price": None if result.base_price is None else str(result.base_price),
        "fill_price": None if result.fill_price is None else str(result.fill_price),
        "commission_usd": str(result.commission),
    })


def _ledger(account: AccountSnapshot, mark: Decimal, equity: Decimal) -> dict[str, Any]:
    return {
        "free_cash_usd": str(account.free_cash), "position_shares": str(account.position_quantity),
        "average_entry_price": str(account.average_entry_price), "mark_price": str(mark),
        "realized_pnl_usd": str(account.realized_pnl), "unrealized_pnl_usd": str(account.unrealized_pnl),
        "fees_usd": str(account.fees), "terminal_equity_usd": str(equity), "reconciled": True,
    }


def _assert_reconciled(account: AccountSnapshot, equity: Decimal) -> None:
    expected = INITIAL_CASH + account.realized_pnl + account.unrealized_pnl - account.fees
    if equity != expected:
        raise ExploratoryBacktestRejected("ledger reconciliation failed")


def _verify_snapshot_files(root: Path, manifest: Mapping[str, Any]) -> None:
    files = manifest.get("files")
    if not isinstance(files, Mapping) or set(files) != {"bars.json", "report.json"}:
        raise ExploratoryBacktestRejected("manifest.files must contain exactly bars.json and report.json hashes")
    for name, expected in files.items():
        if not isinstance(expected, str) or not _is_sha256(expected):
            raise ExploratoryBacktestRejected(f"manifest.files.{name} must be a SHA-256 hex digest")
        actual = _sha256_file(root / name)
        if actual != expected:
            raise ExploratoryBacktestRejected(f"SHA-256 mismatch for {name}")


def _verify_manifest(manifest: Mapping[str, Any], data_root: Path | None) -> None:
    if manifest.get("schema_version") != SNAPSHOT_SCHEMA:
        raise ExploratoryBacktestRejected("manifest schema is not the fixed exploratory TSLA schema")
    if manifest.get("source_relative_path") != SOURCE_RELATIVE_PATH:
        raise ExploratoryBacktestRejected("manifest source_relative_path is outside the fixed TSLA source")
    source_hash = manifest.get("source_sha256")
    if not isinstance(source_hash, str) or not _is_sha256(source_hash):
        raise ExploratoryBacktestRejected("manifest.source_sha256 must be a SHA-256 hex digest")
    if source_hash != SOURCE_SHA256:
        raise ExploratoryBacktestRejected("manifest.source_sha256 is not the fixed TSLA exploratory source")
    if data_root is not None:
        source = _source_under_root(data_root)
        if not source.is_file():
            raise ExploratoryBacktestRejected("fixed raw source file is absent under data_root")
        if _sha256_file(source) != source_hash:
            raise ExploratoryBacktestRejected("raw source SHA-256 mismatch")


def _verify_snapshot_report(report: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    if report.get("schema_version") != SNAPSHOT_SCHEMA:
        raise ExploratoryBacktestRejected("report schema_version is not the fixed exploratory TSLA schema")
    if report.get("symbol") != SYMBOL:
        raise ExploratoryBacktestRejected("report symbol must be TSLA")
    if report.get("rows") != EXPECTED_ROWS or report.get("expected_rows") != EXPECTED_ROWS:
        raise ExploratoryBacktestRejected("report must record exactly 166 expected rows")
    if report.get("qualified") is not False or report.get("formal_backtest_enabled") is not False:
        raise ExploratoryBacktestRejected("snapshot report must remain unqualified and formal-backtest disabled")
    dates = report.get("range")
    if not isinstance(dates, Mapping) or dates.get("start") != START.isoformat() or dates.get("end") != END.isoformat():
        raise ExploratoryBacktestRejected("report range must be the fixed 2026 TSLA interval")
    if report.get("source_sha256") != manifest["source_sha256"]:
        raise ExploratoryBacktestRejected("report source_sha256 does not match manifest")
    if report.get("source_relative_path") != SOURCE_RELATIVE_PATH:
        raise ExploratoryBacktestRejected("report source_relative_path is outside the fixed TSLA source")
    _calendar_version(report)
    _observation_caveat(report)
    _reject_nonzero_corporate_action_marker(report)


def _parse_bars(raw: Sequence[Any]) -> tuple[SnapshotBar, ...]:
    if len(raw) != EXPECTED_ROWS:
        raise ExploratoryBacktestRejected("bars.json must contain exactly 166 rows")
    expected_sessions, schedule = _xnys_sessions()
    parsed: list[SnapshotBar] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ExploratoryBacktestRejected(f"bar {index} must be an object")
        required = {"symbol", "session_date", "open", "high", "low", "close", "volume", "source", "adjustment_status", "event_time", "historical_available_at"}
        if set(item) != required:
            raise ExploratoryBacktestRejected(f"bar {index} has unexpected or missing fields")
        if item["symbol"] != SYMBOL or item["source"] != SOURCE or item["adjustment_status"] != PRICE_BASIS:
            raise ExploratoryBacktestRejected(f"bar {index} violates fixed symbol/source/raw-price scope")
        if item["event_time"] is not None or item["historical_available_at"] is not None:
            raise ExploratoryBacktestRejected(f"bar {index} must retain unknown historical timestamps as null")
        try:
            session = date.fromisoformat(str(item["session_date"]))
        except ValueError as exc:
            raise ExploratoryBacktestRejected(f"bar {index} has invalid session_date") from exc
        if session != expected_sessions[index]:
            raise ExploratoryBacktestRejected("bars must exactly match fixed XNYS sessions; no gaps or duplicates")
        values = {name: _decimal(item[name], f"bar {index}.{name}") for name in ("open", "high", "low", "close", "volume")}
        if any(values[name] <= 0 for name in ("open", "high", "low", "close")) or values["volume"] < 0:
            raise ExploratoryBacktestRejected(f"bar {index} has non-positive price or negative volume")
        if values["low"] > min(values["open"], values["close"]) or values["high"] < max(values["open"], values["close"]) or values["low"] > values["high"]:
            raise ExploratoryBacktestRejected(f"bar {index} has invalid OHLC bounds")
        open_time, close_time = schedule[session]
        parsed.append(SnapshotBar(session, **values, open_time=open_time, close_time=close_time))
    return tuple(parsed)


def _xnys_sessions() -> tuple[tuple[date, ...], dict[date, tuple[datetime, datetime]]]:
    calendar = calendars.get_calendar("XNYS")
    sessions = calendar.sessions_in_range(START.isoformat(), END.isoformat())
    result = tuple(session.date() for session in sessions)
    if len(result) != EXPECTED_ROWS:
        raise AssertionError("fixed XNYS calendar contract changed")
    schedule = {
        session.date(): (
            calendar.session_open(session).to_pydatetime().astimezone(UTC),
            calendar.session_close(session).to_pydatetime().astimezone(UTC),
        )
        for session in sessions
    }
    return result, schedule


def _reject_nonzero_corporate_action_marker(report: Mapping[str, Any]) -> None:
    markers = ("corporate_action_count", "corporate_actions_count", "corporate_actions", "corporate_action_flags", "split_count", "dividend_count")
    present = [name for name in markers if name in report]
    if not present:
        return
    for name in present:
        value = report[name]
        if isinstance(value, Mapping):
            values = value.values()
        elif isinstance(value, (list, tuple)):
            values = value
        else:
            values = (value,)
        for marker in values:
            try:
                zero = _decimal(marker, f"report.{name}") == 0
            except ExploratoryBacktestRejected:
                zero = marker is False or marker is None or marker == "none"
            if not zero:
                raise ExploratoryBacktestRejected("any corporate-action marker must fail closed")


def _calendar_version(report: Mapping[str, Any]) -> str:
    value = report.get("calendar_version")
    if not isinstance(value, str) or not value.strip() or value.strip().lower() in {"current", "latest", "unknown"}:
        raise ExploratoryBacktestRejected("report must contain a fixed calendar_version")
    if value != f"XNYS-exchange_calendars-{calendars.__version__}":
        raise ExploratoryBacktestRejected("report calendar_version does not match installed XNYS calendar")
    return value


def _observation_caveat(report: Mapping[str, Any]) -> str:
    limitations = report.get("limitations")
    if not isinstance(limitations, list) or not all(isinstance(item, str) for item in limitations):
        raise ExploratoryBacktestRejected("report must contain a limitations string list")
    caveat = next((item for item in limitations if "historical_available_at unknown" in item and "exploratory" in item), None)
    if caveat is None:
        raise ExploratoryBacktestRejected("report limitations must retain the historical-availability exploratory caveat")
    return caveat


def _sma(closes: Sequence[Decimal], window: int) -> Decimal | None:
    if len(closes) < window:
        return None
    return sum(closes[-window:], Decimal("0")) / Decimal(window)


def _snapshot_directory(value: Path) -> Path:
    if not isinstance(value, Path):
        raise ExploratoryBacktestRejected("snapshot_dir must be a pathlib.Path")
    root = value.resolve()
    if not root.is_dir():
        raise ExploratoryBacktestRejected("snapshot_dir must be an existing directory")
    for name in ("manifest.json", "bars.json", "report.json"):
        if not (root / name).is_file():
            raise ExploratoryBacktestRejected(f"snapshot_dir is missing {name}")
    return root


def _source_under_root(data_root: Path) -> Path:
    if not isinstance(data_root, Path):
        raise ExploratoryBacktestRejected("data_root must be a pathlib.Path")
    root = data_root.resolve()
    if not root.is_dir():
        raise ExploratoryBacktestRejected("data_root must be an existing directory")
    source = (root / SOURCE_RELATIVE_PATH).resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise ExploratoryBacktestRejected("source path escapes data_root") from exc
    return source


def _read_json(path: Path, label: str) -> Mapping[str, Any] | list[Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            result = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ExploratoryBacktestRejected(f"cannot read valid JSON from {label}") from exc
    if not isinstance(result, (Mapping, list)):
        raise ExploratoryBacktestRejected(f"{label} must be a JSON object or list")
    return result


def _sha256_file(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise ExploratoryBacktestRejected(f"cannot hash {path.name}") from exc


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _decimal(value: Any, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExploratoryBacktestRejected(f"{name} must be a finite decimal") from exc
    if not result.is_finite():
        raise ExploratoryBacktestRejected(f"{name} must be a finite decimal")
    return result


def _report_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
