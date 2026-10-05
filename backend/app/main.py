from datetime import date
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.engines.fefo import expire_lots
from app.modules import consume_log

app = FastAPI(title="Pantryfifo", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "pantryfifo"}

@app.get("/api/items")
def items():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM items")]; c.close(); return rows

@app.get("/api/fridge")
def fridge(layer: str | None = None):
    c = connect()
    q = """SELECT lots.*, items.name, items.layer, items.unit FROM lots
           JOIN items ON items.id=lots.item_id WHERE lots.status='on_shelf'"""
    args = []
    if layer:
        q += " AND items.layer=?"; args.append(layer)
    rows = [dict(r) for r in c.execute(q, args)]; c.close(); return rows

@app.get("/api/alerts")
def alerts():
    c = connect()
    warn = int(c.execute("SELECT value FROM settings WHERE key='warn_days'").fetchone()["value"])
    today = date.today().isoformat()
    rows = [dict(r) for r in c.execute(
        """SELECT lots.*, items.name, items.layer FROM lots JOIN items ON items.id=lots.item_id
           WHERE status='on_shelf' AND qty_remain>0 AND expiry IS NOT NULL""")]
    c.close()
    out = []
    for r in rows:
        if r["expiry"] <= today:
            r["level"] = "expired"
            out.append(r)
        else:
            # simple day diff via fromisoformat
            delta = (date.fromisoformat(r["expiry"]) - date.today()).days
            if delta <= warn:
                r["level"] = "soon"; r["days_left"] = delta; out.append(r)
    return out

class LotIn(BaseModel):
    item_id: int
    qty: float
    expiry: str

@app.post("/api/lots")
def inbound(body: LotIn):
    c = connect()
    item = c.execute("SELECT id FROM items WHERE id=?", (body.item_id,)).fetchone()
    if not item: c.close(); raise HTTPException(404, "item")
    cur = c.execute(
        "INSERT INTO lots(item_id,qty_in,qty_remain,expiry,status,data_quality) VALUES (?,?,?,?,?,?)",
        (body.item_id, body.qty, body.qty, body.expiry, "on_shelf", "clean"))
    c.commit(); lid = cur.lastrowid; c.close(); return {"id": lid}

class ConsumeIn(BaseModel):
    item_id: int
    qty: float
    reason: str = ""
    note: str = ""

_CONSUME_ERR_STATUS = {"reason_required": 400, "qty_non_positive": 400}

@app.post("/api/consume/preview")
def consume_preview(body: ConsumeIn):
    # 预览:只读,列出将扣的 lot 与 take,不写履历、不改余量
    return consume_log.preview_consume(body.item_id, body.qty)

@app.post("/api/consume")
def consume(body: ConsumeIn):
    try:
        return consume_log.confirm_consume(body.item_id, body.qty, body.reason, body.note)
    except consume_log.ConsumeError as e:
        raise HTTPException(_CONSUME_ERR_STATUS.get(e.code, 409), e.payload)

@app.get("/api/consumptions")
def consumptions(lot_id: int | None = None, item_id: int | None = None):
    return consume_log.history(lot_id=lot_id, item_id=item_id)

@app.get("/api/consumptions/reconcile")
def consumptions_reconcile():
    return consume_log.reconcile_by_lot()

@app.post("/api/expire-sweep")
def expire_sweep():
    c = connect()
    lots = [dict(r) for r in c.execute("SELECT * FROM lots WHERE status='on_shelf'")]
    ids = expire_lots(lots, date.today().isoformat())
    for i in ids:
        c.execute("UPDATE lots SET status='expired' WHERE id=?", (i,))
    c.commit(); c.close(); return {"expired_ids": ids}

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows

class SettingIn(BaseModel):
    key: str
    value: str

_EDITABLE_SETTINGS = {"warn_days", "default_reason"}

@app.put("/api/settings")
def put_setting(body: SettingIn):
    # 只写 settings 表;已落库的履历行保持原文,新确认才用新默认
    if body.key not in _EDITABLE_SETTINGS:
        raise HTTPException(400, "unknown_key")
    value = body.value.strip()
    if not value:
        raise HTTPException(400, "empty_value")
    c = connect()
    c.execute("INSERT INTO settings(key,value) VALUES (?,?)"
              " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (body.key, value))
    c.commit(); c.close(); return {"key": body.key, "value": value}
