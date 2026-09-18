from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
import pandas as pd
import pytest
from quant_data.massive_alias_publish import MassiveAliasPublishError, publish_massive_aliases
from quant_data.massive_alias_publish import _validate_window
from datetime import date

NOW=datetime(2026,9,18,12,tzinfo=timezone.utc); DAY="2026-09-17"; OBS="2026-09-18T10:00:00+00:00"

def test_future_bar_session_and_future_evidence_rejected():
 with pytest.raises(MassiveAliasPublishError):
  _validate_window(date(2026, 9, 18), NOW, [NOW])
 with pytest.raises(MassiveAliasPublishError):
  _validate_window(date(2026, 9, 17), NOW, [datetime(2026, 9, 19, tzinfo=timezone.utc)])
def _master(root):
 p=root/"master.csv"; pd.DataFrame({"symbol":["BRK-B"],"status":["active"],"asset_type":["stock"]}).to_csv(p,index=False); return p
def _snap(root):
 s=root/"reference/massive-daily-v1/s"; s.mkdir(parents=True); raw=b"{}"; (s/"response.json").write_bytes(raw); d={"schema_version":"massive-daily-validation-v1","accepted_count":1,"accepted_rows":[0],"rejected_rows":[]}; (s/"diagnostics.json").write_text(json.dumps(d))
 f=pd.DataFrame([["BRKpB",DAY,1.,2.,1.,1.5,3,OBS,OBS,"unknown"]],columns=["symbol","date","open","high","low","close","volume","available_at","retrieved_at","actions_status"]); f.to_parquet(s/"bars.parquet",index=False)
 sh=lambda p:hashlib.sha256(p.read_bytes()).hexdigest(); m={"schema_version":"massive-daily-reference-v1","namespace":"massive-daily-v1","status":"captured","qualified":False,"research_qualified":False,"actions_status":"unknown","source":"https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/{date}","request":{"date":DAY,"adjusted":False,"include_otc":False,"method":"GET","url":f"https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/{DAY}"},"observed_at":OBS,"response_sha256":sh(s/"response.json"),"diagnostics_sha256":sh(s/"diagnostics.json"),"normalized_sha256":sh(s/"bars.parquet")}; (s/"manifest.json").write_text(json.dumps(m)); return s
def _raw_report(root, master, bad=False):
 ref=root/"ref"; ref.mkdir(); raw=ref/"p"; raw.write_bytes(b"x"); allp=ref/"all-tickers.json"; allp.write_text(json.dumps({"tickers":[{"ticker":"BRKpB","primary_exchange":"XNYS"}]})); rh=lambda p:hashlib.sha256(p.read_bytes()).hexdigest(); rm={"schema_version":"massive-ticker-reference-v1","status":"captured","all_tickers_sha256":rh(allp),"pages":[{"raw_relative_path":"p","sha256":rh(raw)}],"observed_end":OBS}; (ref/"manifest.json").write_text(json.dumps(rm)); listing=root/"listing"; listing.mkdir(); reportdir=root/"audit"; reportdir.mkdir(); mastersha=rh(master); rep={"schema_version":"massive-reference-alias-audit-v1","observed_at":OBS,"inputs":{"security_master_sha256":mastersha,"reference_snapshot":str(ref),"reference_all_tickers_sha256":rm["all_tickers_sha256"],"reference_page_hashes":{"p":rh(raw)},"listing_snapshot":str(listing),"listing_sha256":{"x":"y"}},"items":[{"symbol":"BRK-B","classification":"authoritative_alias_candidate","candidates":[{"reference_ticker":"BRKpB"}]}]}; rp=reportdir/"report.json"; rp.write_text(json.dumps(rep)); (reportdir/"manifest.json").write_text(json.dumps({"status":"captured","report_sha256":rh(rp)})); return rp
def _report(root, master, bad=False):
 report = _raw_report(root, master, bad)
 (root / "listing/gap-evidence.json").write_text(json.dumps({"observed_at": OBS}))
 return report

def test_publish_alias(monkeypatch,tmp_path):
 master=_master(tmp_path); snap=_snap(tmp_path); report=_report(tmp_path,master)
 (tmp_path/"catalogue").mkdir(); (tmp_path/"catalogue/us-daily-browser-v1.json").write_text(json.dumps({"schema_version":"us-daily-browser-v1","series":[]}))
 monkeypatch.setattr("quant_data.massive_alias_publish._listing_rows",lambda _: ([{"primary_symbol":"BRK-B","security_name":"x","exchange":"NYSE","aliases":{"CQS Symbol":"BRKpB","NASDAQ Symbol":"BRK-B"}}],{"x":"y"},""))
 result=publish_massive_aliases(tmp_path,master,report,[snap],now=NOW); assert result["published"]==1 and result["returned"]==1
 out=next((tmp_path/"bars/daily/provider=massive/namespace=massive-current-alias-daily-v1").rglob("bars.parquet")); frame=pd.read_parquet(out); assert frame.loc[0,"symbol"]=="BRK-B" and frame.loc[0,"provider_symbol"]=="BRKpB" and frame.loc[0,"research_qualified"]==False
 again=publish_massive_aliases(tmp_path,master,report,[snap],now=NOW); assert again["published"]==0 and again["skipped"]==1
 catalogue=json.loads((tmp_path/"catalogue/us-daily-browser-v1.json").read_text()); assert catalogue["series"][0]["namespace"]=="massive-current-alias-daily-v1"
def test_bad_hash_fails(monkeypatch,tmp_path):
 master=_master(tmp_path); snap=_snap(tmp_path); report=_report(tmp_path,master); monkeypatch.setattr("quant_data.massive_alias_publish._listing_rows",lambda _: ([{"primary_symbol":"BRK-B","security_name":"x","exchange":"NYSE","aliases":{"CQS Symbol":"BRKpB","NASDAQ Symbol":"BRK-B"}}],{"x":"y"},"")); (report.parent/"manifest.json").write_text(json.dumps({"status":"captured","report_sha256":"bad"}))
 with pytest.raises(MassiveAliasPublishError,match="alias_report_integrity_invalid"): publish_massive_aliases(tmp_path,master,report,[snap],now=NOW)

def test_old_target_is_rejected_even_when_snapshot_observation_is_recent(monkeypatch,tmp_path):
 master=_master(tmp_path); snap=_snap(tmp_path); report=_report(tmp_path,master)
 monkeypatch.setattr("quant_data.massive_alias_publish._listing_rows",lambda _: ([{"primary_symbol":"BRK-B","security_name":"x","exchange":"NYSE","aliases":{"CQS Symbol":"BRKpB","NASDAQ Symbol":"BRK-B"}}],{"x":"y"},""))
 # The raw snapshot was observed recently, but its *bar session* is now >31
 # days old.  Retrieval time must not widen the current-alias window.
 with pytest.raises(MassiveAliasPublishError,match="alias_evidence_outside_31_day_window"):
  publish_massive_aliases(tmp_path,master,report,[snap],now=datetime(2026,11,1,tzinfo=timezone.utc))

def test_duplicate_provider_candidate_is_rejected_not_first_wins(monkeypatch,tmp_path):
 master=_master(tmp_path); pd.DataFrame({"symbol":["BRK-B","OTHER"],"status":["active","active"],"asset_type":["stock","stock"]}).to_csv(master,index=False)
 snap=_snap(tmp_path); report=_report(tmp_path,master); payload=json.loads(report.read_text()); payload["items"].append({"symbol":"OTHER","classification":"authoritative_alias_candidate","candidates":[{"reference_ticker":"BRKpB"}]}); report.write_text(json.dumps(payload)); (report.parent/"manifest.json").write_text(json.dumps({"status":"captured","report_sha256":hashlib.sha256(report.read_bytes()).hexdigest()}))
 rows=[{"primary_symbol":"BRK-B","security_name":"x","exchange":"NYSE","aliases":{"CQS Symbol":"BRKpB","NASDAQ Symbol":"BRK-B"}},{"primary_symbol":"OTHER","security_name":"x","exchange":"NYSE","aliases":{"CQS Symbol":"BRKpB","NASDAQ Symbol":"OTHER"}}]
 monkeypatch.setattr("quant_data.massive_alias_publish._listing_rows",lambda _: (rows,{"x":"y"},""))
 with pytest.raises(MassiveAliasPublishError,match="alias_report_no_unique_candidates"):
  publish_massive_aliases(tmp_path,master,report,[snap],now=NOW)

def test_existing_price_conflict_is_preserved_and_reported(monkeypatch,tmp_path):
 master=_master(tmp_path); snap=_snap(tmp_path); report=_report(tmp_path,master); (tmp_path/"catalogue").mkdir(); (tmp_path/"catalogue/us-daily-browser-v1.json").write_text(json.dumps({"schema_version":"us-daily-browser-v1","series":[]}))
 monkeypatch.setattr("quant_data.massive_alias_publish._listing_rows",lambda _: ([{"primary_symbol":"BRK-B","security_name":"x","exchange":"NYSE","aliases":{"CQS Symbol":"BRKpB","NASDAQ Symbol":"BRK-B"}}],{"x":"y"},""))
 assert publish_massive_aliases(tmp_path,master,report,[snap],now=NOW)["published"]==1
 output=next((tmp_path/"bars/daily/provider=massive/namespace=massive-current-alias-daily-v1").rglob("bars.parquet")); before=output.read_bytes()
 bars=snap/"bars.parquet"; changed=pd.read_parquet(bars); changed.loc[0,"close"]=1.25; changed.to_parquet(bars,index=False); manifest=json.loads((snap/"manifest.json").read_text()); manifest["normalized_sha256"]=hashlib.sha256(bars.read_bytes()).hexdigest(); (snap/"manifest.json").write_text(json.dumps(manifest))
 result=publish_massive_aliases(tmp_path,master,report,[snap],now=NOW)
 assert result["failed"]==1 and output.read_bytes()==before

def test_rerun_repairs_missing_alias_catalogue_entry(monkeypatch,tmp_path):
 master=_master(tmp_path); snap=_snap(tmp_path); report=_report(tmp_path,master); (tmp_path/"catalogue").mkdir(); catalogue=tmp_path/"catalogue/us-daily-browser-v1.json"; catalogue.write_text(json.dumps({"schema_version":"us-daily-browser-v1","series":[]}))
 monkeypatch.setattr("quant_data.massive_alias_publish._listing_rows",lambda _: ([{"primary_symbol":"BRK-B","security_name":"x","exchange":"NYSE","aliases":{"CQS Symbol":"BRKpB","NASDAQ Symbol":"BRK-B"}}],{"x":"y"},""))
 publish_massive_aliases(tmp_path,master,report,[snap],now=NOW); catalogue.write_text(json.dumps({"schema_version":"us-daily-browser-v1","series":[]}))
 result=publish_massive_aliases(tmp_path,master,report,[snap],now=NOW)
 assert result["skipped"]==1
 assert json.loads(catalogue.read_text())["series"], "idempotent bars must repair their catalogue view"
