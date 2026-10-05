"""消费原因履历：确认扣减必须带原因码，并在同一事务落下履历行。

对账不变量（每个成功确认事务内成立）：
  每个 deduction 恰好一行 consume_lines；
  line.take == qty_before - qty_after == lots 表该 lot 的余量减少量。
缺原因码 / 原因码非法 / 库存不足 / 数量非正 -> 整单回滚，lots 与履历都不动。
BEGIN IMMEDIATE 将并发确认串行化：后一个事务在锁内重新读到扣减后的余量，
因此两笔并发消费的履历行数、全层减少量、回包 take 三方必然对得上。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import connect
from app.engines.fefo import consume_fefo

router = APIRouter(prefix="/api", tags=["consume_reason"])


class ConsumeIn(BaseModel):
    item_id: int
    qty: float
    reason_code: str | None = None
    note: str = ""


class DefaultReasonIn(BaseModel):
    code: str


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _active_reason(conn, code: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM reason_codes WHERE code=? AND active=1", (code,)
    ).fetchone()
    return row is not None


def resolve_reason(conn, given: str | None) -> str:
    """确认必须显式带原因码；缺码/空码/未启用码 -> 整单失败。

    默认原因码只由前端预填后随请求显式上送，因此改默认只影响「新的一次确认」，
    已落库 consume_lines.reason_code 永远不会被回头改写。
    """
    code = (given or "").strip()
    if not code:
        raise HTTPException(400, "reason_required")
    if not _active_reason(conn, code):
        raise HTTPException(400, "invalid_reason")
    return code


def _load_lots(conn, item_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM lots WHERE item_id=? AND status='on_shelf' AND qty_remain>0",
        (item_id,),
    )]


def _plan(conn, item_id: int, qty: float) -> dict:
    if not conn.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
        raise HTTPException(404, "item")
    return consume_fefo(_load_lots(conn, item_id), qty)


@router.get("/reasons")
def list_reasons():
    c = connect()
    rows = [dict(r) for r in c.execute(
        "SELECT code,label FROM reason_codes WHERE active=1 ORDER BY code")]
    c.close()
    return rows


@router.get("/consume/preview")
def consume_preview(item_id: int, qty: float):
    """只列出将扣的 lot 与 take，不写履历、不改余量。"""
    c = connect()
    try:
        result = _plan(c, item_id, qty)
    finally:
        c.close()
    if not result["ok"] and result["reason"] == "qty_non_positive":
        raise HTTPException(400, "qty_non_positive")
    # 库存不足也照常回预览（ok=false），前端可提示缺口；本端点只读。
    return {"item_id": item_id, "qty": qty, **result}


@router.post("/consume")
def consume_confirm(body: ConsumeIn):
    c = connect()
    try:
        c.execute("PRAGMA busy_timeout=10000")
        c.isolation_level = None  # 自己管事务
        c.execute("BEGIN IMMEDIATE")  # 并发确认在此排队，杜绝交错读写

        # 原因解析失败必须在任何写操作之前抛错 -> rollback，两表都不动。
        code = resolve_reason(c, body.reason_code)
        result = _plan(c, body.item_id, body.qty)
        if not result["ok"]:
            c.execute("ROLLBACK")
            if result["reason"] == "qty_non_positive":
                raise HTTPException(400, "qty_non_positive")
            raise HTTPException(409, result)

        now = _now()
        cur = c.execute(
            "INSERT INTO consume_orders(item_id,qty,reason_code,note,created_at) "
            "VALUES (?,?,?,?,?)",
            (body.item_id, body.qty, code, body.note, now),
        )
        order_id = cur.lastrowid

        deductions = result["deductions"]
        for d in deductions:
            row = c.execute(
                "SELECT qty_remain FROM lots WHERE id=?", (d["lot_id"],)
            ).fetchone()
            before = float(row["qty_remain"])
            take = float(d["take"])
            after = before - take
            # 与 lots 更新同一条语句的事务里落行，不可能只减余量不落履历。
            c.execute(
                "UPDATE lots SET qty_remain=? WHERE id=?", (after, d["lot_id"]))
            if after <= 0:
                c.execute(
                    "UPDATE lots SET status='consumed', qty_remain=0 WHERE id=?",
                    (d["lot_id"],))
                after = 0.0
            c.execute(
                "INSERT INTO consume_lines(order_id,lot_id,take,qty_before,qty_after,"
                "reason_code,created_at) VALUES (?,?,?,?,?,?,?)",
                (order_id, d["lot_id"], take, before, after, code, now),
            )

        c.commit()
    except HTTPException:
        # 409/400 路径已 ROLLBACK；兜底再回滚一次（如未开始提交）。
        try:
            c.execute("ROLLBACK")
        except Exception:
            pass
        raise
    except Exception:
        try:
            c.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        c.close()

    return {"ok": True, "order_id": order_id, "reason_code": code,
            "deductions": deductions, "short": 0.0}


@router.get("/consume/history")
def consume_history(lot_id: int | None = None, item_id: int | None = None):
    """履历行（每笔确认 × 每个被扣 lot 一行），可按批号过滤。"""
    q = """SELECT l.id, l.order_id, l.lot_id, l.take, l.qty_before, l.qty_after,
                  l.reason_code, l.created_at,
                  o.item_id, i.name AS item_name, i.unit, lots.expiry
           FROM consume_lines l
           JOIN consume_orders o ON o.id=l.order_id
           JOIN items i ON i.id=o.item_id
           JOIN lots ON lots.id=l.lot_id
           WHERE 1=1"""
    args = []
    if lot_id is not None:
        q += " AND l.lot_id=?"; args.append(lot_id)
    if item_id is not None:
        q += " AND o.item_id=?"; args.append(item_id)
    q += " ORDER BY l.id"
    c = connect()
    rows = [dict(r) for r in c.execute(q, args)]
    c.close()
    return rows


@router.get("/consume/reconcile")
def consume_reconcile(layer: str | None = None):
    """按 lot 对账：履历 take 加总 vs 全层该批少掉的量（qty_in - qty_remain）。"""
    q = """SELECT lots.id AS lot_id, lots.item_id, items.name AS item_name,
                  items.layer, items.unit, lots.expiry, lots.status,
                  lots.qty_in, lots.qty_remain,
                  COALESCE(SUM(cl.take),0) AS history_take,
                  COUNT(cl.id) AS line_count
           FROM lots
           JOIN items ON items.id=lots.item_id
           LEFT JOIN consume_lines cl ON cl.lot_id=lots.id
           WHERE 1=1"""
    args = []
    if layer:
        q += " AND items.layer=?"; args.append(layer)
    q += " GROUP BY lots.id ORDER BY lots.id"
    c = connect()
    rows = [dict(r) for r in c.execute(q, args)]
    c.close()
    eps = 1e-6
    out = []
    for r in rows:
        layer_decrease = float(r["qty_in"]) - float(r["qty_remain"])
        history_take = float(r["history_take"])
        r["layer_decrease"] = round(layer_decrease, 6)
        r["history_take"] = round(history_take, 6)
        r["balanced"] = abs(layer_decrease - history_take) < eps
        out.append(r)
    return {
        "lots": out,
        "balanced": all(r["balanced"] for r in out),
        "total_history_take": round(sum(r["history_take"] for r in out), 6),
        "total_layer_decrease": round(sum(r["layer_decrease"] for r in out), 6),
        "total_lines": sum(int(r["line_count"]) for r in out),
    }


@router.put("/settings/default-reason")
def set_default_reason(body: DefaultReasonIn):
    code = body.code.strip()
    c = connect()
    if not code:
        c.close()
        raise HTTPException(400, "reason_required")
    if not _active_reason(c, code):
        c.close()
        raise HTTPException(400, "invalid_reason")
    c.execute(
        "INSERT INTO settings(key,value) VALUES ('default_reason',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (code,))
    c.commit(); c.close()
    # 只影响此后新确认；已落库 consume_lines.reason_code 一概不动。
    return {"default_reason": code}
