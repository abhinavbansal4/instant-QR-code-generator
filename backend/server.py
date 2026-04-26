from fastapi import FastAPI, APIRouter, HTTPException, Query
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError
import os
import asyncio
import base64
import logging
import secrets
import string
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, EmailStr
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone
import resend


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

resend.api_key = os.environ.get('RESEND_API_KEY', '')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'onboarding@resend.dev')
SENDER_NAME = os.environ.get('SENDER_NAME', 'QR Events')
FROM_HEADER = f"{SENDER_NAME} <{SENDER_EMAIL}>"

app = FastAPI()
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ---------------- Helpers ----------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


CODE_ALPHABET = string.ascii_uppercase + string.digits
CODE_ALPHABET = ''.join(c for c in CODE_ALPHABET if c not in 'O0I1')  # avoid confusables


def gen_event_code() -> str:
    a = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(4))
    b = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(4))
    return f"{a}-{b}"


def _strip_data_url(b64: str) -> bytes:
    if ',' in b64 and b64.lstrip().startswith('data:'):
        b64 = b64.split(',', 1)[1]
    return base64.b64decode(b64)


# ---------------- Models ----------------
class EventMeta(BaseModel):
    name: str = ''
    year: str = ''
    date: str = ''
    venue: str = ''
    organizer_email: str = ''
    qr_template: str = '{event_name} {year} - {name}'
    qr_size: int = 320
    f1_label: str = ''
    f2_label: str = ''


class CreateEventRequest(BaseModel):
    name: str
    year: str = ''
    date: str = ''
    venue: str = ''
    organizer_email: str = ''
    qr_template: str = '{event_name} {year} - {name}'
    qr_size: int = 320
    f1_label: str = ''
    f2_label: str = ''


class UpdateEventRequest(EventMeta):
    pass


class RecipientIn(BaseModel):
    recipient_id: str
    name: str
    email: str = ''
    f1: str = ''
    f2: str = ''
    qr_text: str = ''


class BulkRecipientsRequest(BaseModel):
    recipients: List[RecipientIn]
    replace: bool = True  # replace all existing recipients for the event


class CheckinRequest(BaseModel):
    recipient_id: str
    count: int = 1


class WalkInRequest(BaseModel):
    name: str
    email: str = ''
    f1: str = ''
    f2: str = ''
    count: int = 1


# ---------------- Email models ----------------
class SendQrEmailRequest(BaseModel):
    recipient_email: EmailStr
    recipient_name: str
    event_name: str
    event_year: Optional[str] = ''
    event_date: Optional[str] = ''
    event_venue: Optional[str] = ''
    qr_text: str
    qr_png_base64: str
    extra_note: Optional[str] = ''


class SendZipEmailRequest(BaseModel):
    organizer_email: EmailStr
    event_name: str
    event_year: Optional[str] = ''
    recipient_count: int = 0
    zip_base64: str
    zip_filename: str = 'qr_codes.zip'


# ---------------- Indexes ----------------
@app.on_event("startup")
async def ensure_indexes():
    await db.events.create_index("event_code", unique=True)
    await db.event_recipients.create_index(
        [("event_code", 1), ("recipient_id", 1)], unique=True
    )
    await db.event_checkins.create_index(
        [("event_code", 1), ("recipient_id", 1)], unique=True
    )
    await db.event_checkins.create_index([("event_code", 1), ("at", -1)])


# ---------------- Public ----------------
@api_router.get("/")
async def root():
    return {"message": "QR Events API ready"}


@api_router.get("/email/health")
async def email_health():
    return {
        "configured": bool(resend.api_key),
        "sender": SENDER_EMAIL,
        "sender_name": SENDER_NAME,
    }


# ============== EVENTS ==============
async def _stats(event_code: str) -> Dict[str, Any]:
    total = await db.event_recipients.count_documents({"event_code": event_code})
    in_count = await db.event_checkins.count_documents({"event_code": event_code})
    # sum people from checkins
    cur = db.event_checkins.aggregate([
        {"$match": {"event_code": event_code}},
        {"$group": {"_id": None, "people": {"$sum": "$count"}}}
    ])
    people = 0
    async for row in cur:
        people = int(row.get("people") or 0)
    return {
        "total": total,
        "checked_in": in_count,
        "pending": max(0, total - in_count),
        "people": people,
    }


@api_router.post("/events")
async def create_event(req: CreateEventRequest):
    # generate unique code with retry
    for _ in range(8):
        code = gen_event_code()
        doc = {
            "event_code": code,
            "name": req.name,
            "year": req.year,
            "date": req.date,
            "venue": req.venue,
            "organizer_email": req.organizer_email,
            "qr_template": req.qr_template,
            "qr_size": int(req.qr_size or 320),
            "f1_label": req.f1_label,
            "f2_label": req.f2_label,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        try:
            await db.events.insert_one(doc)
            doc.pop("_id", None)
            return {"event_code": code, "event": doc, "stats": await _stats(code)}
        except DuplicateKeyError:
            continue
    raise HTTPException(500, "Could not allocate event code, try again")


@api_router.get("/events/{event_code}")
async def get_event(event_code: str):
    ev = await db.events.find_one({"event_code": event_code}, {"_id": 0})
    if not ev:
        raise HTTPException(404, "Event not found")
    return {"event": ev, "stats": await _stats(event_code)}


@api_router.put("/events/{event_code}")
async def update_event(event_code: str, req: UpdateEventRequest):
    res = await db.events.update_one(
        {"event_code": event_code},
        {"$set": {**req.model_dump(), "updated_at": now_iso()}}
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Event not found")
    ev = await db.events.find_one({"event_code": event_code}, {"_id": 0})
    return {"event": ev, "stats": await _stats(event_code)}


@api_router.delete("/events/{event_code}")
async def delete_event(event_code: str):
    await db.event_checkins.delete_many({"event_code": event_code})
    await db.event_recipients.delete_many({"event_code": event_code})
    res = await db.events.delete_one({"event_code": event_code})
    if res.deleted_count == 0:
        raise HTTPException(404, "Event not found")
    return {"status": "deleted", "event_code": event_code}


@api_router.get("/events/{event_code}/stats")
async def event_stats(event_code: str):
    ev = await db.events.find_one({"event_code": event_code}, {"_id": 0, "name": 1, "year": 1})
    if not ev:
        raise HTTPException(404, "Event not found")
    return {"event_code": event_code, **ev, **(await _stats(event_code))}


# ============== RECIPIENTS ==============
@api_router.post("/events/{event_code}/recipients/bulk")
async def bulk_set_recipients(event_code: str, req: BulkRecipientsRequest):
    ev = await db.events.find_one({"event_code": event_code}, {"_id": 0, "event_code": 1})
    if not ev:
        raise HTTPException(404, "Event not found")
    if req.replace:
        await db.event_recipients.delete_many({"event_code": event_code})
        # also clear orphan check-ins (recipient_ids that no longer exist will be filtered, but leave checkins for now)
    if req.recipients:
        docs = [{
            "event_code": event_code,
            "recipient_id": r.recipient_id,
            "name": r.name,
            "email": r.email,
            "f1": r.f1, "f2": r.f2,
            "qr_text": r.qr_text,
            "created_at": now_iso(),
        } for r in req.recipients]
        # bulk insert; on partial duplicate (when not replacing) skip
        try:
            await db.event_recipients.insert_many(docs, ordered=False)
        except Exception as e:
            # ignore duplicate-key bulk errors
            logger.warning(f"insert_many recipients had errors: {e}")
    return {"event_code": event_code, "count": len(req.recipients), "stats": await _stats(event_code)}


@api_router.get("/events/{event_code}/recipients")
async def list_recipients(event_code: str):
    ev = await db.events.find_one({"event_code": event_code}, {"_id": 0, "event_code": 1})
    if not ev:
        raise HTTPException(404, "Event not found")
    cur = db.event_recipients.find({"event_code": event_code}, {"_id": 0}).sort("created_at", 1)
    items = await cur.to_list(length=20000)
    return {"event_code": event_code, "recipients": items, "count": len(items)}


# ============== CHECK-INS ==============
@api_router.post("/events/{event_code}/checkin")
async def record_checkin(event_code: str, req: CheckinRequest):
    # Find the recipient first to get their meta (for read-side denormalization)
    rec = await db.event_recipients.find_one(
        {"event_code": event_code, "recipient_id": req.recipient_id},
        {"_id": 0}
    )
    if not rec:
        raise HTTPException(404, "Recipient not found in this event")

    doc = {
        "event_code": event_code,
        "recipient_id": req.recipient_id,
        "name": rec.get("name", ""),
        "email": rec.get("email", ""),
        "qr_text": rec.get("qr_text", ""),
        "count": max(1, int(req.count or 1)),
        "at": now_iso(),
    }
    try:
        await db.event_checkins.insert_one(doc)
        doc.pop("_id", None)
        return {"status": "checked_in", "checkin": doc, "stats": await _stats(event_code)}
    except DuplicateKeyError:
        existing = await db.event_checkins.find_one(
            {"event_code": event_code, "recipient_id": req.recipient_id},
            {"_id": 0}
        )
        raise HTTPException(409, {"status": "already_checked_in", "checkin": existing})


@api_router.post("/events/{event_code}/walkin")
async def walkin(event_code: str, req: WalkInRequest):
    name = (req.name or '').strip()
    if not name:
        raise HTTPException(400, "Name is required")
    ev = await db.events.find_one({"event_code": event_code}, {"_id": 0})
    if not ev:
        raise HTTPException(404, "Event not found")
    template = ev.get("qr_template") or "{event_name} {year} - {name}"
    qr_text = (template
        .replace("{event_name}", ev.get("name") or "")
        .replace("{year}", str(ev.get("year") or ""))
        .replace("{name}", name)
        .replace("{email}", req.email or "")
        .replace("{f1}", req.f1 or "")
        .replace("{f2}", req.f2 or ""))
    recipient_id = "walk_" + uuid.uuid4().hex[:12]
    rec = {
        "event_code": event_code,
        "recipient_id": recipient_id,
        "name": name,
        "email": (req.email or '').strip(),
        "f1": req.f1, "f2": req.f2,
        "qr_text": qr_text,
        "created_at": now_iso(),
        "is_walkin": True,
    }
    await db.event_recipients.insert_one(rec)
    rec.pop("_id", None)

    chk = {
        "event_code": event_code,
        "recipient_id": recipient_id,
        "name": name,
        "email": (req.email or '').strip(),
        "qr_text": qr_text,
        "count": max(1, int(req.count or 1)),
        "at": now_iso(),
        "is_walkin": True,
    }
    await db.event_checkins.insert_one(chk)
    chk.pop("_id", None)

    return {
        "recipient": rec,
        "checkin": chk,
        "event": {
            "name": ev.get("name", ""),
            "year": str(ev.get("year") or ""),
            "date": ev.get("date", ""),
            "venue": ev.get("venue", ""),
            "qr_size": ev.get("qr_size", 320),
        },
        "stats": await _stats(event_code),
    }


class LookupRequest(BaseModel):
    qr_text: str


@api_router.post("/events/{event_code}/lookup")
async def lookup_by_qr(event_code: str, req: LookupRequest):
    rec = await db.event_recipients.find_one(
        {"event_code": event_code, "qr_text": req.qr_text.strip()},
        {"_id": 0}
    )
    if not rec:
        return {"found": False}
    existing = await db.event_checkins.find_one(
        {"event_code": event_code, "recipient_id": rec["recipient_id"]},
        {"_id": 0}
    )
    return {"found": True, "recipient": rec, "checkin": existing}


@api_router.get("/events/{event_code}/checkins")
async def list_checkins(event_code: str, limit: int = Query(50, ge=1, le=500)):
    cur = db.event_checkins.find({"event_code": event_code}, {"_id": 0}).sort("at", -1).limit(limit)
    items = await cur.to_list(length=limit)
    return {"event_code": event_code, "checkins": items, "count": len(items)}


@api_router.delete("/events/{event_code}/checkins")
async def reset_checkins(event_code: str, confirm: str = ""):
    if confirm != "YES":
        raise HTTPException(400, "Add ?confirm=YES to reset all check-ins for this event")
    res = await db.event_checkins.delete_many({"event_code": event_code})
    return {"status": "reset", "deleted": res.deleted_count, "stats": await _stats(event_code)}


# ============== EMAIL ==============
def _ticket_html(req: SendQrEmailRequest, cid: str, event_code: Optional[str] = None) -> str:
    rows = []
    if req.event_date:
        rows.append(f"<tr><td style='padding:4px 0;color:#6b675e'>Date</td><td style='padding:4px 0 4px 16px'>{req.event_date}</td></tr>")
    if req.event_venue:
        rows.append(f"<tr><td style='padding:4px 0;color:#6b675e'>Venue</td><td style='padding:4px 0 4px 16px'>{req.event_venue}</td></tr>")
    if event_code:
        rows.append(f"<tr><td style='padding:4px 0;color:#6b675e'>Event code</td><td style='padding:4px 0 4px 16px;font-family:monospace'>{event_code}</td></tr>")
    rows_html = ''.join(rows) or "<tr><td colspan='2' style='color:#6b675e;font-size:13px;padding-top:4px'>Your event entry pass is below.</td></tr>"
    note = f"<p style='margin:18px 0 0;color:#3a3a3a;font-size:14px;line-height:1.6'>{req.extra_note}</p>" if req.extra_note else ''
    title = f"{req.event_name} {req.event_year}".strip()
    return f"""
<!doctype html><html><body style="margin:0;padding:0;background:#f7f4ee;font-family:Arial,Helvetica,sans-serif;color:#1a1a1a">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f7f4ee;padding:32px 12px">
    <tr><td align="center">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border:1px solid #e7e2d6;border-radius:14px;overflow:hidden">
        <tr><td style="padding:24px 28px 8px">
          <div style="font-size:12px;color:#c2410c;letter-spacing:.14em;text-transform:uppercase;font-weight:700">Event Pass</div>
          <h1 style="margin:6px 0 0;font-size:22px;letter-spacing:-.01em">{title}</h1>
        </td></tr>
        <tr><td style="padding:6px 28px 0">
          <p style="margin:0;font-size:15px">Hi <b>{req.recipient_name}</b>,</p>
          <p style="margin:8px 0 16px;font-size:14px;color:#3a3a3a;line-height:1.6">
            Here is your personal QR code. Please show this at the entrance for check-in.
          </p>
          <table role="presentation" cellpadding="0" cellspacing="0" style="font-size:14px;border-collapse:collapse">{rows_html}</table>
        </td></tr>
        <tr><td align="center" style="padding:18px 28px 8px">
          <div style="display:inline-block;padding:14px;background:#fff;border:1px solid #e7e2d6;border-radius:12px">
            <img src="cid:{cid}" alt="QR" width="220" height="220" style="display:block;width:220px;height:220px"/>
          </div>
          <div style="margin-top:10px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:#6b675e;word-break:break-all">{req.qr_text}</div>
        </td></tr>
        <tr><td style="padding:0 28px 24px">{note}</td></tr>
        <tr><td style="padding:14px 28px;background:#faf7f1;border-top:1px solid #efeadd;font-size:12px;color:#6b675e">
          QR PNG is also attached for your records.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def _zip_html(req: SendZipEmailRequest) -> str:
    title = f"{req.event_name} {req.event_year}".strip()
    return f"""
<!doctype html><html><body style="margin:0;padding:0;background:#f7f4ee;font-family:Arial,Helvetica,sans-serif;color:#1a1a1a">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f7f4ee;padding:32px 12px">
    <tr><td align="center">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff;border:1px solid #e7e2d6;border-radius:14px;overflow:hidden">
        <tr><td style="padding:24px 28px">
          <div style="font-size:12px;color:#c2410c;letter-spacing:.14em;text-transform:uppercase;font-weight:700">QR Bundle</div>
          <h1 style="margin:6px 0 14px;font-size:22px;letter-spacing:-.01em">{title}</h1>
          <p style="margin:0 0 8px;font-size:14px;line-height:1.6">
            Attached is a ZIP with <b>{req.recipient_count}</b> QR code PNG file(s) for your event.
          </p>
          <p style="margin:0;font-size:13px;color:#6b675e;line-height:1.6">
            File: <code style="background:#f4f1ea;padding:2px 6px;border-radius:4px">{req.zip_filename}</code>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


@api_router.post("/send-qr-email")
async def send_qr_email(req: SendQrEmailRequest, event_code: Optional[str] = Query(None)):
    if not resend.api_key:
        raise HTTPException(500, "Email not configured: missing RESEND_API_KEY")
    try:
        png_bytes = _strip_data_url(req.qr_png_base64)
    except Exception as e:
        raise HTTPException(400, f"Invalid qr_png_base64: {e}")
    cid = f"qr-{uuid.uuid4().hex[:8]}"
    safe_name = req.recipient_name.replace('/', '_').strip() or 'recipient'
    filename = f"{safe_name}_QR.png"
    params = {
        "from": FROM_HEADER,
        "to": [req.recipient_email],
        "subject": f"Your QR pass for {req.event_name} {req.event_year}".strip(),
        "html": _ticket_html(req, cid, event_code),
        "attachments": [{"filename": filename, "content": list(png_bytes), "content_id": cid}],
    }
    try:
        result = await asyncio.to_thread(resend.Emails.send, params)
        return {"status": "success", "email_id": result.get("id"), "to": req.recipient_email}
    except Exception as e:
        logger.error(f"send-qr-email failed: {e}")
        raise HTTPException(502, f"Resend error: {e}")


@api_router.post("/send-zip-email")
async def send_zip_email(req: SendZipEmailRequest):
    if not resend.api_key:
        raise HTTPException(500, "Email not configured: missing RESEND_API_KEY")
    try:
        zip_bytes = _strip_data_url(req.zip_base64)
    except Exception as e:
        raise HTTPException(400, f"Invalid zip_base64: {e}")
    params = {
        "from": FROM_HEADER,
        "to": [req.organizer_email],
        "subject": f"QR codes ZIP — {req.event_name} {req.event_year}".strip(),
        "html": _zip_html(req),
        "attachments": [{"filename": req.zip_filename or "qr_codes.zip", "content": list(zip_bytes)}],
    }
    try:
        result = await asyncio.to_thread(resend.Emails.send, params)
        return {"status": "success", "email_id": result.get("id"), "to": req.organizer_email}
    except Exception as e:
        logger.error(f"send-zip-email failed: {e}")
        raise HTTPException(502, f"Resend error: {e}")


# ---------------- Mount ----------------
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
