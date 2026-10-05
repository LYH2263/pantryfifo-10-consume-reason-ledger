"""消费原因履历:预览只读,确认必须带原因码,扣减与履历在同一事务落库。

不变量:
- 确认扣减必须带非空原因码,否则整单失败,lots 与履历都不动。
- 一次确认的 lots 扣减与履历行(consumptions 头 + consumption_lines 行)同事务提交,
  不允许余量扣了而履历缺行,也不允许履历落了而 lots 没扣。
- 履历行只 INSERT 不 UPDATE:改默认原因码不会改写已落库的履历字。
"""
import json
from datetime import datetime, timezone

from app.db import connect
from app.engines.fefo import consume_fefo


class ConsumeError(Exception):
    """业务失败,code 为稳定错误码,payload 为回包细节。"""

    def __init__(self, code, payload=None):
        super().__init__(code)
        self.code = code
        self.payload = payload if payload is not None else code


def _on_shelf_lots(c, item_id: int) -> list[dict]:
    return [dict(r) for r in c.execute(
        "SELECT * FROM lots WHERE item_id=? AND status='on_shelf' AND qty_remain>0",
        (item_id,))]


def preview_consume(item_id: int, qty: float) -> dict:
    """预览:列出将扣的 lot 与 take。只读,不写履历、不改余量。"""
    c = connect()
    try:
        return consume_fefo(_on_shelf_lots(c, item_id), qty)
    finally:
        c.close()


def confirm_consume(item_id: int, qty: float, reason: str, note: str = "") -> dict:
    """确认扣减:原因码必填;lots 扣减与履历行在同一事务,并发下两两串行。"""
    reason = (reason or "").strip()
    if not reason:
        raise ConsumeError("reason_required")
    c = connect()
    try:
        # BEGIN IMMEDIATE 先拿写锁再读,并发确认串行化,避免两单读到同一余量各扣一遍。
        c.execute("BEGIN IMMEDIATE")
        result = consume_fefo(_on_shelf_lots(c, item_id), qty)
        if not result["ok"]:
            raise ConsumeError(result["reason"], result)
        now = datetime.now(timezone.utc).isoformat()
        cur = c.execute(
            "INSERT INTO consumptions(reason,note,result_json,created_at) VALUES (?,?,?,?)",
            (reason, note or "", json.dumps(result), now))
        consumption_id = cur.lastrowid
        for d in result["deductions"]:
            cur = c.execute(
                "UPDATE lots SET qty_remain = qty_remain - ?"
                " WHERE id=? AND status='on_shelf' AND qty_remain >= ?",
                (d["take"], d["lot_id"], d["take"]))
            if cur.rowcount != 1:
                raise ConsumeError("lot_changed", {"lot_id": d["lot_id"]})
            rem = c.execute("SELECT qty_remain FROM lots WHERE id=?",
                            (d["lot_id"],)).fetchone()["qty_remain"]
            if rem <= 0:
                c.execute("UPDATE lots SET status='consumed', qty_remain=0 WHERE id=?",
                          (d["lot_id"],))
            c.execute(
                "INSERT INTO consumption_lines"
                "(consumption_id,lot_id,item_id,take,reason,created_at) VALUES (?,?,?,?,?,?)",
                (consumption_id, d["lot_id"], item_id, d["take"], reason, now))
        c.commit()
        result["consumption_id"] = consumption_id
        result["reason_code"] = reason
        return result
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def history(lot_id: int | None = None, item_id: int | None = None,
            limit: int = 500) -> list[dict]:
    """履历明细行,新的在前。"""
    q = """SELECT cl.id, cl.consumption_id, cl.lot_id, cl.item_id, cl.take, cl.reason,
                  cl.created_at, i.name AS item_name, i.layer, i.unit, l.expiry
           FROM consumption_lines cl
           JOIN items i ON i.id = cl.item_id
           LEFT JOIN lots l ON l.id = cl.lot_id"""
    args: list = []
    conds = []
    if lot_id is not None:
        conds.append("cl.lot_id=?"); args.append(lot_id)
    if item_id is not None:
        conds.append("cl.item_id=?"); args.append(item_id)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY cl.id DESC LIMIT ?"; args.append(limit)
    c = connect()
    try:
        return [dict(r) for r in c.execute(q, args)]
    finally:
        c.close()


def reconcile_by_lot() -> list[dict]:
    """按批号对账:履历 take 加总 vs 该批全层减少量(qty_in - qty_remain)。"""
    c = connect()
    try:
        rows = [dict(r) for r in c.execute(
            """SELECT l.id AS lot_id, l.item_id, i.name AS item_name, i.layer, i.unit,
                      l.qty_in, l.qty_remain, l.status, l.expiry,
                      ROUND(l.qty_in - l.qty_remain, 6) AS reduced,
                      COALESCE((SELECT ROUND(SUM(cl.take), 6) FROM consumption_lines cl
                                WHERE cl.lot_id = l.id), 0) AS take_sum
               FROM lots l JOIN items i ON i.id = l.item_id
               ORDER BY l.id""")]
    finally:
        c.close()
    for r in rows:
        r["diff"] = round(r["reduced"] - r["take_sum"], 6)
        r["balanced"] = abs(r["diff"]) < 1e-6
    return rows
