"""consume_log 模块测试:纯 sqlite3,不依赖 fastapi,可直接 python 运行。"""
import os
import tempfile
import threading

from app import seed
from app.db import connect
from app.modules import consume_log


def _fresh_db():
    os.environ["DATA_DIR"] = tempfile.mkdtemp()
    seed.init_db()
    c = connect()
    c.executescript("DELETE FROM consumption_lines; DELETE FROM consumptions;"
                    "DELETE FROM lots; DELETE FROM items;")
    c.commit(); c.close()


def _lot(item_id, qty, expiry="2027-01-01"):
    c = connect()
    c.execute("INSERT OR IGNORE INTO items(id,name,layer,unit) VALUES (?,?,?,?)",
              (item_id, "item%d" % item_id, "upper", "个"))
    cur = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality)"
        " VALUES (?,?,?,?,?,?)", (item_id, qty, qty, expiry, "on_shelf", "clean"))
    lid = cur.lastrowid
    c.commit(); c.close()
    return lid


def _remain(lot_id):
    c = connect()
    r = c.execute("SELECT qty_remain FROM lots WHERE id=?", (lot_id,)).fetchone()["qty_remain"]
    c.close()
    return r


def _counts():
    """(履历头行数, 履历行数, 履历 take 合计)"""
    c = connect()
    h = c.execute("SELECT COUNT(*) c FROM consumptions").fetchone()["c"]
    n = c.execute("SELECT COUNT(*) c FROM consumption_lines").fetchone()["c"]
    s = c.execute("SELECT COALESCE(SUM(take),0) s FROM consumption_lines").fetchone()["s"]
    c.close()
    return h, n, s


def test_preview_is_readonly():
    _fresh_db()
    l1 = _lot(1, 2, "2027-01-01")
    l2 = _lot(1, 3, "2027-02-01")
    r = consume_log.preview_consume(1, 4)
    assert r["ok"] and [d["take"] for d in r["deductions"]] == [2, 2]
    assert [d["lot_id"] for d in r["deductions"]] == [l1, l2]
    # 不写履历、不改余量
    assert _remain(l1) == 2 and _remain(l2) == 3
    assert _counts() == (0, 0, 0)


def test_missing_reason_fails_whole_order():
    _fresh_db()
    l1 = _lot(1, 5)
    for bad in ("", "   ", None):
        try:
            consume_log.confirm_consume(1, 2, bad)
            raise AssertionError("should raise")
        except consume_log.ConsumeError as e:
            assert e.code == "reason_required"
    # lots 与履历都不动
    assert _remain(l1) == 5
    assert _counts() == (0, 0, 0)


def test_non_positive_qty_fails_clean():
    _fresh_db()
    l1 = _lot(1, 5)
    try:
        consume_log.confirm_consume(1, 0, "日常消耗")
        raise AssertionError("should raise")
    except consume_log.ConsumeError as e:
        assert e.code == "qty_non_positive"
    assert _remain(l1) == 5 and _counts() == (0, 0, 0)


def test_confirm_writes_header_and_lines():
    _fresh_db()
    l1 = _lot(1, 2, "2027-01-01")
    l2 = _lot(1, 3, "2027-02-01")
    r = consume_log.confirm_consume(1, 4, "日常消耗", "早餐")
    assert r["ok"] and r["consumption_id"] and r["reason_code"] == "日常消耗"
    assert [d["take"] for d in r["deductions"]] == [2, 2]
    assert _remain(l1) == 0 and _remain(l2) == 1
    # 一头两行,take 合计 == 回包合计 == 全层减少量
    assert _counts() == (1, 2, 4)
    c = connect()
    rows = [dict(x) for x in c.execute("SELECT * FROM consumption_lines ORDER BY id")]
    head = dict(c.execute("SELECT * FROM consumptions").fetchone())
    c.close()
    assert [x["lot_id"] for x in rows] == [l1, l2]
    assert [x["take"] for x in rows] == [2, 2]
    assert all(x["reason"] == "日常消耗" for x in rows)
    assert all(x["consumption_id"] == r["consumption_id"] for x in rows)
    assert head["reason"] == "日常消耗" and head["note"] == "早餐"


def test_short_rolls_back_everything():
    _fresh_db()
    l1 = _lot(1, 1)
    try:
        consume_log.confirm_consume(1, 5, "日常消耗")
        raise AssertionError("should raise")
    except consume_log.ConsumeError as e:
        assert e.code == "short" and e.payload["short"] == 4
    assert _remain(l1) == 1
    assert _counts() == (0, 0, 0)


def test_concurrent_confirms_reconcile():
    """两笔成功消费并发:履历行数、全层减少量、回包 take 三方对账。"""
    _fresh_db()
    l1 = _lot(1, 10)
    barrier = threading.Barrier(2)
    out, errs = [], []

    def work(qty):
        try:
            barrier.wait(timeout=10)
            out.append(consume_log.confirm_consume(1, qty, "日常消耗"))
        except Exception as e:
            errs.append(e)

    ts = [threading.Thread(target=work, args=(3,)),
          threading.Thread(target=work, args=(4,))]
    for t in ts: t.start()
    for t in ts: t.join(30)
    assert not errs, errs
    assert len(out) == 2
    # 回包 take 各自等于各自请求量
    takes = sorted(sum(d["take"] for d in r["deductions"]) for r in out)
    assert takes == [3, 4]
    # 履历:两头两行,合计 7;余量:10-7=3 —— 不允许余量减两笔履历一行,也不许反过来
    assert _counts() == (2, 2, 7)
    assert abs(_remain(l1) - 3) < 1e-9
    row = [r for r in consume_log.reconcile_by_lot() if r["lot_id"] == l1][0]
    assert row["balanced"] and row["take_sum"] == 7 and row["reduced"] == 7


def test_concurrent_stress_no_lost_update():
    """多轮并发确认:总扣减量必须等于履历合计,无丢更新。"""
    _fresh_db()
    l1 = _lot(1, 100)
    errs = []

    def work():
        try:
            for _ in range(5):
                consume_log.confirm_consume(1, 1, "日常消耗")
        except Exception as e:
            errs.append(e)

    ts = [threading.Thread(target=work) for _ in range(2)]
    for t in ts: t.start()
    for t in ts: t.join(60)
    assert not errs, errs
    assert _counts() == (10, 10, 10)
    assert abs(_remain(l1) - 90) < 1e-9


def test_default_reason_change_keeps_history_text():
    """改默认原因码不改写已落库履历;新确认才用新原因。"""
    _fresh_db()
    _lot(1, 10)
    consume_log.confirm_consume(1, 1, "日常消耗")
    c = connect()
    c.execute("UPDATE settings SET value='变质丢弃' WHERE key='default_reason'")
    c.commit(); c.close()
    consume_log.confirm_consume(1, 2, "变质丢弃")
    reasons = sorted(r["reason"] for r in consume_log.history())
    assert reasons == sorted(["日常消耗", "变质丢弃"])
    c = connect()
    v = c.execute("SELECT value FROM settings WHERE key='default_reason'").fetchone()["value"]
    c.close()
    assert v == "变质丢弃"


def test_reconcile_by_lot_balances():
    """履历页按批号把 take 加总,与全层该批少掉的量对上。"""
    _fresh_db()
    l1 = _lot(1, 2, "2027-01-01")
    l2 = _lot(1, 3, "2027-02-01")
    l3 = _lot(2, 7)
    consume_log.confirm_consume(1, 4, "做菜")
    consume_log.confirm_consume(2, 2.5, "做菜")
    rec = {r["lot_id"]: r for r in consume_log.reconcile_by_lot()}
    assert rec[l1]["take_sum"] == 2 and rec[l1]["reduced"] == 2 and rec[l1]["balanced"]
    assert rec[l2]["take_sum"] == 2 and rec[l2]["reduced"] == 2 and rec[l2]["balanced"]
    assert rec[l3]["take_sum"] == 2.5 and rec[l3]["reduced"] == 2.5 and rec[l3]["balanced"]
    lines = consume_log.history(lot_id=l2)
    assert len(lines) == 1 and lines[0]["take"] == 2


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok", fn.__name__)
    print("%d tests passed" % len(fns))
