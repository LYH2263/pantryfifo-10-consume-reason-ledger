"""API 级测试:需要 fastapi + httpx(Docker 镜像内),本地缺依赖时跳过。"""
import os
import tempfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _client():
    os.environ["DATA_DIR"] = tempfile.mkdtemp()
    return TestClient(app)


def test_reason_required_400_and_nothing_moves():
    with _client() as client:
        r = client.post("/api/consume", json={"item_id": 1, "qty": 1})
        assert r.status_code == 400 and r.json()["detail"] == "reason_required"
        r = client.post("/api/consume", json={"item_id": 1, "qty": 1, "reason": "  "})
        assert r.status_code == 400
        lots = {l["id"]: l for l in client.get("/api/fridge").json()}
        assert lots[1]["qty_remain"] == 2 and lots[2]["qty_remain"] == 1
        assert client.get("/api/consumptions").json() == []


def test_preview_lists_lots_without_side_effect():
    with _client() as client:
        r = client.post("/api/consume/preview", json={"item_id": 2, "qty": 5})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] and body["deductions"] == [
            {"lot_id": 3, "take": 5, "expiry": "2026-11-01"}]
        lots = {l["id"]: l for l in client.get("/api/fridge").json()}
        assert lots[3]["qty_remain"] == 12
        assert client.get("/api/consumptions").json() == []


def test_confirm_then_history_and_reconcile():
    with _client() as client:
        r = client.post("/api/consume", json={"item_id": 1, "qty": 2, "reason": "日常消耗"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] and body["reason_code"] == "日常消耗" and body["consumption_id"]
        # FEFO:先到期批 #2 扣 1,再 #1 扣 1 → 两行履历
        assert sorted(d["take"] for d in body["deductions"]) == [1, 1]
        lines = client.get("/api/consumptions").json()
        assert len(lines) == 2 and sum(l["take"] for l in lines) == 2
        assert all(l["reason"] == "日常消耗" for l in lines)
        rec = client.get("/api/consumptions/reconcile").json()
        assert rec and all(row["balanced"] for row in rec)
        by_lot = {row["lot_id"]: row for row in rec}
        assert by_lot[1]["take_sum"] == 1 and by_lot[2]["take_sum"] == 1


def test_settings_put_keeps_history_text():
    with _client() as client:
        client.post("/api/consume", json={"item_id": 2, "qty": 1, "reason": "日常消耗"})
        r = client.put("/api/settings", json={"key": "default_reason", "value": "变质丢弃"})
        assert r.status_code == 200
        assert client.get("/api/settings").json()["default_reason"] == "变质丢弃"
        # 已落库履历保持原文
        assert [l["reason"] for l in client.get("/api/consumptions").json()] == ["日常消耗"]
        # 新确认才用新原因
        client.post("/api/consume", json={"item_id": 2, "qty": 1, "reason": "变质丢弃"})
        reasons = sorted(l["reason"] for l in client.get("/api/consumptions").json())
        assert reasons == sorted(["日常消耗", "变质丢弃"])
        assert client.put("/api/settings", json={"key": "nope", "value": "x"}).status_code == 400
        assert client.put("/api/settings", json={"key": "default_reason", "value": " "}).status_code == 400
