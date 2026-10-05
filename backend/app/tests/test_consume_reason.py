import threading
import pytest
from fastapi import HTTPException

from app import seed
from app.db import connect
from app.modules.consume_reason import (
    ConsumeIn, DefaultReasonIn, consume_confirm, consume_preview,
    consume_history, consume_reconcile, set_default_reason,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seed.init_db()
    c = connect()
    c.execute("DELETE FROM lots")  # 只保留 items/reasons/settings，FEFO 顺序由测试批次决定
    # 干净的测试批次：早到期 qty 2，晚到期 qty 10（item 1 = 牛奶）
    early = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) "
        "VALUES (1,2,2,'2026-10-10','on_shelf','clean')").lastrowid
    late = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) "
        "VALUES (1,10,10,'2026-10-20','on_shelf','clean')").lastrowid
    c.commit(); c.close()
    return {"early": early, "late": late}


def _lot_remain(lot_id):
    c = connect()
    row = c.execute("SELECT qty_remain,status FROM lots WHERE id=?", (lot_id,)).fetchone()
    c.close()
    return float(row["qty_remain"]), row["status"]


def _count(table):
    c = connect()
    n = c.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
    c.close()
    return n


def test_preview_lists_lots_and_take_but_writes_nothing(db):
    e, L = db["early"], db["late"]
    before = {lid: _lot_remain(lid) for lid in (e, L)}
    p = consume_preview(item_id=1, qty=4)
    assert p["ok"] is True
    # 跨两批：先拿早到期 lot 全部 2，再从晚到期 lot 拿 2
    assert [(d["lot_id"], d["take"]) for d in p["deductions"]] == [(e, 2), (L, 2)]
    # 余量与履历都不动
    assert {lid: _lot_remain(lid) for lid in (e, L)} == before
    assert _count("consume_lines") == 0
    assert _count("consume_orders") == 0


def test_missing_reason_fails_whole_order(db):
    e, L = db["early"], db["late"]
    # 默认码存在（seed 写入 EAT），但请求未显式带码 —— 仍须整单失败，默认码不兜底
    c = connect()
    assert c.execute("SELECT value FROM settings WHERE key='default_reason'").fetchone()["value"] == "EAT"
    c.close()
    with pytest.raises(HTTPException) as ex:
        consume_confirm(ConsumeIn(item_id=1, qty=1))
    assert ex.value.status_code == 400 and ex.value.detail == "reason_required"
    # lots 与履历都不动
    assert _lot_remain(e) == (2.0, "on_shelf")
    assert _count("consume_lines") == 0
    assert _count("consume_orders") == 0

    # 非法原因码同样整单失败
    with pytest.raises(HTTPException) as ex2:
        consume_confirm(ConsumeIn(item_id=1, qty=1, reason_code="NOPE"))
    assert ex2.value.status_code == 400 and ex2.value.detail == "invalid_reason"
    assert _count("consume_lines") == 0
    assert _lot_remain(L) == (10.0, "on_shelf")


def test_confirm_writes_one_line_per_deduction_and_stamps_reason(db):
    e, L = db["early"], db["late"]
    r = consume_confirm(ConsumeIn(item_id=1, qty=4, reason_code="COOK"))
    assert r["ok"] and r["reason_code"] == "COOK"
    assert [d["take"] for d in r["deductions"]] == [2, 2]
    # 两个被扣 lot -> 恰好两行履历，take 与余量减少量逐行相等
    c = connect()
    lines = [dict(x) for x in c.execute(
        "SELECT lot_id,take,qty_before,qty_after,reason_code FROM consume_lines ORDER BY id")]
    c.close()
    assert len(lines) == 2
    assert [(l["lot_id"], l["take"]) for l in lines] == [(e, 2), (L, 2)]
    for l in lines:
        assert round(l["qty_before"] - l["qty_after"], 9) == round(l["take"], 9)
        assert l["reason_code"] == "COOK"
    assert _lot_remain(e) == (0.0, "consumed")
    assert _lot_remain(L) == (8.0, "on_shelf")


def test_short_rolls_back_both_lots_and_history(db):
    e, L = db["early"], db["late"]
    with pytest.raises(HTTPException) as ex:
        consume_confirm(ConsumeIn(item_id=1, qty=99, reason_code="EAT"))
    assert ex.value.status_code == 409
    assert _count("consume_lines") == 0 and _count("consume_orders") == 0
    assert _lot_remain(e) == (2.0, "on_shelf")
    assert _lot_remain(L) == (10.0, "on_shelf")


def test_two_concurrent_consumes_reconcile(db):
    """两笔成功并发：履历行数、全层减少量、回包 take 三方对账。"""
    e, L = db["early"], db["late"]
    results, errors = [], []
    barrier = threading.Barrier(2)

    def worker(qty):
        try:
            barrier.wait()
            results.append(consume_confirm(ConsumeIn(item_id=1, qty=qty, reason_code="EAT")))
        except HTTPException as ex:
            errors.append(ex)

    t1 = threading.Thread(target=worker, args=(4,))
    t2 = threading.Thread(target=worker, args=(3,))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert errors == [] and len(results) == 2
    # 回包 take 合计 = 7（一笔跨两批 2+2，一笔从晚到期 lot 拿 3）
    resp_take = round(sum(d["take"] for r in results for d in r["deductions"]), 9)
    assert resp_take == 7
    resp_lines = sum(len(r["deductions"]) for r in results)
    assert resp_lines == 3

    # 履历 3 行、take 合计 7，单头 2 笔
    c = connect()
    n_lines = c.execute("SELECT COUNT(*) c FROM consume_lines").fetchone()["c"]
    h_take = float(c.execute("SELECT COALESCE(SUM(take),0) s FROM consume_lines").fetchone()["s"])
    n_orders = c.execute("SELECT COUNT(*) c FROM consume_orders").fetchone()["c"]
    c.close()
    assert n_orders == 2 and n_lines == 3 and round(h_take, 9) == 7

    # 全层余量：早 lot 空、晚 lot 剩 5，合计减少 7
    assert _lot_remain(e) == (0.0, "consumed")
    assert _lot_remain(L) == (5.0, "on_shelf")

    rec = consume_reconcile()
    by_lot = {r["lot_id"]: r for r in rec["lots"] if r["lot_id"] in (e, L)}
    assert by_lot[e]["history_take"] == 2 and by_lot[e]["layer_decrease"] == 2
    assert by_lot[L]["history_take"] == 5 and by_lot[L]["layer_decrease"] == 5
    assert rec["balanced"] is True
    assert rec["total_history_take"] == rec["total_layer_decrease"] == 7
    # 每个被扣 lot 都有行，绝无「减两笔只落一行」
    assert by_lot[e]["line_count"] == 1 and by_lot[L]["line_count"] == 2


def test_concurrent_overdemand_exactly_one_commits_and_still_balanced(db):
    """两笔并发抢 12 库存、各要 8：一个成功一个 409，绝不两笔都减或履历错行。"""
    e, L = db["early"], db["late"]
    outcomes = []
    barrier = threading.Barrier(2)

    def worker():
        try:
            barrier.wait()
            outcomes.append(("ok", consume_confirm(ConsumeIn(item_id=1, qty=8, reason_code="EAT"))))
        except HTTPException as ex:
            outcomes.append(("err", ex.status_code))

    t1 = threading.Thread(target=worker); t2 = threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()

    assert sorted(s for s, _ in outcomes) == ["err", "ok"]
    assert [v for s, v in outcomes if s == "err"] == [409]
    c = connect()
    n_orders = c.execute("SELECT COUNT(*) c FROM consume_orders").fetchone()["c"]
    n_lines = c.execute("SELECT COUNT(*) c FROM consume_lines").fetchone()["c"]
    h_take = float(c.execute("SELECT COALESCE(SUM(take),0) s FROM consume_lines").fetchone()["s"])
    c.close()
    # 成功那笔跨两批：早 lot 拿 2、晚 lot 拿 6，共 2 行
    assert n_orders == 1 and n_lines == 2 and round(h_take, 9) == 8
    assert _lot_remain(e) == (0.0, "consumed")
    assert _lot_remain(L) == (4.0, "on_shelf")
    assert consume_reconcile()["balanced"] is True


def test_change_default_does_not_rewrite_history(db):
    # 默认 EAT（前端预填上送）：确认一笔，落库字 = EAT
    set_default_reason(DefaultReasonIn(code="EAT"))
    consume_confirm(ConsumeIn(item_id=1, qty=1, reason_code="EAT"))
    # 改默认码：旧履历字不得被改写；新一次确认（显式带新默认）才用新码
    set_default_reason(DefaultReasonIn(code="WASTE"))
    consume_confirm(ConsumeIn(item_id=1, qty=1, reason_code="WASTE"))
    c = connect()
    codes = [r["reason_code"] for r in c.execute("SELECT reason_code FROM consume_lines ORDER BY id")]
    c.close()
    assert codes == ["EAT", "WASTE"]


def test_history_sum_by_lot_matches_layer_decrease(db):
    L = db["late"]
    consume_confirm(ConsumeIn(item_id=1, qty=3, reason_code="EAT"))
    consume_confirm(ConsumeIn(item_id=1, qty=2, reason_code="COOK"))
    rows = consume_history(lot_id=L)
    # 早 lot 先被清空(2)，两笔共 5：晚 lot 上 1+2=3
    assert round(sum(r["take"] for r in rows), 9) == 3
    rec = {r["lot_id"]: r for r in consume_reconcile()["lots"]}
    assert rec[L]["history_take"] == rec[L]["layer_decrease"] == 3
    assert rec[db["early"]]["history_take"] == rec[db["early"]]["layer_decrease"] == 2
