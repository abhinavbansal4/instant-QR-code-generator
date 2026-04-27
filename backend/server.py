from contextlib import asynccontextmanager
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
from pydantic import BaseModel, EmailStr
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone
import resend

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

mongo_url = os.environ['MONGO_URL']
db_name = os.environ['DB_NAME']

resend.api_key = os.environ.get('RESEND_API_KEY', '')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'onboarding@resend.dev')
SENDER_NAME = os.environ.get('SENDER_NAME', 'QR Events')
FROM_HEADER = f"{SENDER_NAME} <{SENDER_EMAIL}>"
CORS_ORIGINS = os.environ.get('CORS_ORIGINS', '*').split(',')

# DB client created once at startup via lifespan
client: AsyncIOMotorClient = None
db = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, db
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    await db.events.create_index("event_code", unique=True)
    await db.event_recipients.create_index([("event_code", 1), ("recipient_id", 1)], unique=True)
    await db.event_checkins.create_index([("event_code", 1), ("recipient_id", 1)], unique=True)
    await db.event_checkins.create_index([("event_code", 1), ("at", -1)])
    logger.info("DB connected and indexes ensured")
    yield
    client.close()
    logger.info("DB connection closed")

app = FastAPI(lifespan=lifespan)
api_router = APIRouter(prefix="")

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------- Helpers --------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

CODE_CHARS = ''.join(c for c in string.ascii_uppercase + string.digits if c not in 'O0I1')

def gen_event_code() -> str:
    return f"{''.join(secrets.choice(CODE_CHARS) for _ in range(4))}-{''.join(secrets.choice(CODE_CHARS) for _ in range(4))}"

def strip_data_url(b64: str) -> bytes:
    if ',' in b64 and b64.lstrip().startswith('data:'):
        b64 = b64.split(',', 1)[1]
    return base64.b64decode(b64)

async def get_event_or_404(event_code: str) -> dict:
    ev = await db.events.find_one({"event_code": event_code}, {"_id": 0})
    if not ev:
        raise HTTPException(404, "Event not found")
    return ev

# -------- Models --------
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

class CreateEventRequest(EventMeta):
    name: str

class RecipientIn(BaseModel):
    recipient_id: str
    name: str
    email: str = ''
    f1: str = ''
    f2: str = ''
    qr_text: str = ''

class BulkRecipientsRequest(BaseModel):
    recipients: List[RecipientIn]
    replace: bool = True

class CheckinRequest(BaseModel):
    recipient_id: str
    count: int = 1

class WalkInRequest(BaseModel):
    name: str
    email: str = ''
    f1: str = ''
    f2: str = ''
    count: int = 1

class LookupRequest(BaseModel):
    qr_text: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None

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

# -------- Stats helper --------
async def get_stats(event_code: str) -> Dict[str, Any]:
    total, in_count = await asyncio.gather(
        db.event_recipients.count_documents({"event_code": event_code}),
        db.event_checkins.count_documents({"event_code": event_code}),
    )
    people = 0
    async for row in db.event_checkins.aggregate([
        {"$match": {"event_code": event_code}},
        {"$group": {"_id": None, "people": {"$sum": "$count"}}}
    ]):
        people = int(row.get("people") or 0)
    return {"total": total, "checked_in": in_count, "pending": max(0, total - in_count), "people": people}

# -------- Routes --------
@api_router.get("/")
async def root():
    return {"message": "QR Events API ready"}

@api_router.get("/email/health")
async def email_health():
    return {"configured": bool(resend.api_key), "sender": SENDER_EMAIL, "sender_name": SENDER_NAME}

# Events
@api_router.post("/events")
async def create_event(req: CreateEventRequest):
    for _ in range(8):
        code = gen_event_code()
        doc = {
            "event_code": code,
            **req.model_dump(),
            "qr_size": int(req.qr_size or 320),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        try:
            await db.events.insert_one(doc)
            doc.pop("_id", None)
            return {"event_code": code, "event": doc, "stats": await get_stats(code)}
        except DuplicateKeyError:
            continue
    raise HTTPException(500, "Could not allocate event code, try again")

@api_router.get("/events/{event_code}")
async def get_event(event_code: str):
    ev = await get_event_or_404(event_code)
    return {"event": ev, "stats": await get_stats(event_code)}

@api_router.put("/events/{event_code}")
async def update_event(event_code: str, req: EventMeta):
    res = await db.events.update_one(
        {"event_code": event_code},
        {"$set": {**req.model_dump(), "updated_at": now_iso()}}
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Event not found")
    ev = await db.events.find_one({"event_code": event_code}, {"_id": 0})
    return {"event": ev, "stats": await get_stats(event_code)}

@api_router.delete("/events/{event_code}")
async def delete_event(event_code: str):
    await asyncio.gather(
        db.event_checkins.delete_many({"event_code": event_code}),
        db.event_recipients.delete_many({"event_code": event_code}),
    )
    res = await db.events.delete_one({"event_code": event_code})
    if res.deleted_count == 0:
        raise HTTPException(404, "Event not found")
    return {"status": "deleted", "event_code": event_code}

@api_router.get("/events/{event_code}/stats")
async def event_stats(event_code: str):
    ev = await db.events.find_one({"event_code": event_code}, {"_id": 0, "name": 1, "year": 1})
    if not ev:
        raise HTTPException(404, "Event not found")
    return {"event_code": event_code, **ev, **(await get_stats(event_code))}

# Recipients
@api_router.post("/events/{event_code}/recipients/bulk")
async def bulk_set_recipients(event_code: str, req: BulkRecipientsRequest):
    await get_event_or_404(event_code)
    if req.replace:
        await db.event_recipients.delete_many({"event_code": event_code})
    if req.recipients:
        docs = [{
            "event_code": event_code,
            "recipient_id": r.recipient_id,
            "name": r.name, "email": r.email,
            "f1": r.f1, "f2": r.f2,
            "qr_text": r.qr_text,
            "created_at": now_iso(),
        } for r in req.recipients]
        try:
            await db.event_recipients.insert_many(docs, ordered=False)
        except Exception as e:
            logger.warning(f"insert_many recipients had errors: {e}")
    return {"event_code": event_code, "count": len(req.recipients), "stats": await get_stats(event_code)}

@api_router.get("/events/{event_code}/recipients")
async def list_recipients(event_code: str):
    await get_event_or_404(event_code)
    items = await db.event_recipients.find({"event_code": event_code}, {"_id": 0}).sort("created_at", 1).to_list(20000)
    return {"event_code": event_code, "recipients": items, "count": len(items)}

# Check-ins
@api_router.post("/events/{event_code}/checkin")
async def record_checkin(event_code: str, req: CheckinRequest):
    rec = await db.event_recipients.find_one(
        {"event_code": event_code, "recipient_id": req.recipient_id}, {"_id": 0}
    )
    if not rec:
        raise HTTPException(404, "Recipient not found in this event")
    doc = {
        "event_code": event_code,
        "recipient_id": req.recipient_id,
        "name": rec.get("name", ""), "email": rec.get("email", ""),
        "qr_text": rec.get("qr_text", ""),
        "count": max(1, int(req.count or 1)),
        "at": now_iso(),
    }
    try:
        await db.event_checkins.insert_one(doc)
        doc.pop("_id", None)
        return {"status": "checked_in", "checkin": doc, "stats": await get_stats(event_code)}
    except DuplicateKeyError:
        existing = await db.event_checkins.find_one(
            {"event_code": event_code, "recipient_id": req.recipient_id}, {"_id": 0}
        )
        raise HTTPException(409, {"status": "already_checked_in", "checkin": existing})

@api_router.post("/events/{event_code}/walkin")
async def walkin(event_code: str, req: WalkInRequest):
    name = (req.name or '').strip()
    if not name:
        raise HTTPException(400, "Name is required")
    ev = await get_event_or_404(event_code)
    template = ev.get("qr_template") or "{event_name} {year} - {name}"
    qr_text = (template
        .replace("{event_name}", ev.get("name") or "")
        .replace("{year}", str(ev.get("year") or ""))
        .replace("{name}", name)
        .replace("{email}", req.email or "")
        .replace("{f1}", req.f1 or "")
        .replace("{f2}", req.f2 or ""))
    recipient_id = "walk_" + uuid.uuid4().hex[:12]
    now = now_iso()
    rec = {"event_code": event_code, "recipient_id": recipient_id, "name": name,
           "email": req.email.strip(), "f1": req.f1, "f2": req.f2,
           "qr_text": qr_text, "created_at": now, "is_walkin": True}
    chk = {"event_code": event_code, "recipient_id": recipient_id, "name": name,
           "email": req.email.strip(), "qr_text": qr_text,
           "count": max(1, int(req.count or 1)), "at": now, "is_walkin": True}
    await asyncio.gather(
        db.event_recipients.insert_one(rec),
        db.event_checkins.insert_one(chk),
    )
    rec.pop("_id", None); chk.pop("_id", None)
    return {
        "recipient": rec, "checkin": chk,
        "event": {"name": ev.get("name", ""), "year": str(ev.get("year") or ""),
                  "date": ev.get("date", ""), "venue": ev.get("venue", ""),
                  "qr_size": ev.get("qr_size", 320)},
        "stats": await get_stats(event_code),
    }

@api_router.post("/events/{event_code}/lookup")
async def lookup_by_qr(event_code: str, req: LookupRequest):
    query: Dict[str, Any] = {"event_code": event_code}
    if req.qr_text:
        query["qr_text"] = req.qr_text.strip()
    elif req.email:
        query["email"] = {"$regex": f"^{req.email.strip()}$", "$options": "i"}
    elif req.name:
        query["name"] = {"$regex": f"^{req.name.strip()}$", "$options": "i"}
    else:
        raise HTTPException(400, "Provide qr_text, email, or name")
    rec = await db.event_recipients.find_one(query, {"_id": 0})
    if not rec:
        return {"found": False}
    existing = await db.event_checkins.find_one(
        {"event_code": event_code, "recipient_id": rec["recipient_id"]}, {"_id": 0}
    )
    return {"found": True, "recipient": rec, "checkin": existing}

@api_router.post("/events/{event_code}/lookup-multi")
async def lookup_multi(event_code: str, req: LookupRequest):
    query: Dict[str, Any] = {"event_code": event_code}
    if req.email:
        query["email"] = {"$regex": req.email.strip(), "$options": "i"}
    elif req.name:
        query["name"] = {"$regex": req.name.strip(), "$options": "i"}
    else:
        raise HTTPException(400, "Provide email or name")
    items = await db.event_recipients.find(query, {"_id": 0}).limit(10).to_list(10)
    checkins = await asyncio.gather(*[
        db.event_checkins.find_one({"event_code": event_code, "recipient_id": r["recipient_id"]}, {"_id": 0})
        for r in items
    ])
    for r, c in zip(items, checkins):
        r["checkin"] = c
    return {"matches": items, "count": len(items)}

@api_router.get("/events/{event_code}/checkins")
async def list_checkins(event_code: str, limit: int = Query(50, ge=1, le=500)):
    items = await db.event_checkins.find({"event_code": event_code}, {"_id": 0}).sort("at", -1).limit(limit).to_list(limit)
    return {"event_code": event_code, "checkins": items, "count": len(items)}

@api_router.delete("/events/{event_code}/checkins")
async def reset_checkins(event_code: str, confirm: str = ""):
    if confirm != "YES":
        raise HTTPException(400, "Add ?confirm=YES to reset all check-ins for this event")
    res = await db.event_checkins.delete_many({"event_code": event_code})
    return {"status": "reset", "deleted": res.deleted_count, "stats": await get_stats(event_code)}

# Email
def ticket_html(req: SendQrEmailRequest, cid: str, event_code: Optional[str] = None) -> str:
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
    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#f7f4ee;font-family:Arial,Helvetica,sans-serif;color:#1a1a1a">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f7f4ee;padding:32px 12px">
    <tr><td align="center">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border:1px solid #e7e2d6;border-radius:14px;overflow:hidden">
        <tr><td style="padding:24px 28px 8px">
          <div style="font-size:12px;color:#c2410c;letter-spacing:.14em;text-transform:uppercase;font-weight:700">Event Pass</div>
          <h1 style="margin:6px 0 0;font-size:22px;letter-spacing:-.01em">{title}</h1>
        </td></tr>
        <tr><td style="padding:6px 28px 0">
          <p style="margin:0;font-size:15px">Hi <b>{req.recipient_name}</b>,</p>
          <p style="margin:8px 0 16px;font-size:14px;color:#3a3a3a;line-height:1.6">Here is your personal QR code. Please show this at the entrance for check-in.</p>
          <table role="presentation" cellpadding="0" cellspacing="0" style="font-size:14px;border-collapse:collapse">{rows_html}</table>
        </td></tr>
        <tr><td align="center" style="padding:18px 28px 8px">
          <div style="display:inline-block;padding:14px;background:#fff;border:1px solid #e7e2d6;border-radius:12px">
            <img src="cid:{cid}" alt="QR" width="220" height="220" style="display:block;width:220px;height:220px"/>
          </div>
          <div style="margin-top:10px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:#6b675e;word-break:break-all">{req.qr_text}</div>
        </td></tr>
        <tr><td style="padding:0 28px 24px">{note}</td></tr>
        <tr><td style="padding:14px 28px;background:#faf7f1;border-top:1px solid #efeadd;font-size:12px;color:#6b675e">QR PNG is also attached for your records.</td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""

def zip_html(req: SendZipEmailRequest) -> str:
    title = f"{req.event_name} {req.event_year}".strip()
    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#f7f4ee;font-family:Arial,Helvetica,sans-serif;color:#1a1a1a">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f7f4ee;padding:32px 12px">
    <tr><td align="center">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff;border:1px solid #e7e2d6;border-radius:14px;overflow:hidden">
        <tr><td style="padding:24px 28px">
          <div style="font-size:12px;color:#c2410c;letter-spacing:.14em;text-transform:uppercase;font-weight:700">QR Bundle</div>
          <h1 style="margin:6px 0 14px;font-size:22px;letter-spacing:-.01em">{title}</h1>
          <p style="margin:0 0 8px;font-size:14px;line-height:1.6">Attached is a ZIP with <b>{req.recipient_count}</b> QR code PNG file(s) for your event.</p>
          <p style="margin:0;font-size:13px;color:#6b675e;line-height:1.6">File: <code style="background:#f4f1ea;padding:2px 6px;border-radius:4px">{req.zip_filename}</code></p>
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
        png_bytes = strip_data_url(req.qr_png_base64)
    except Exception as e:
        raise HTTPException(400, f"Invalid qr_png_base64: {e}")
    cid = f"qr-{uuid.uuid4().hex[:8]}"
    safe_name = req.recipient_name.replace('/', '_').strip() or 'recipient'
    params = {
        "from": FROM_HEADER,
        "to": [req.recipient_email],
        "subject": f"Your QR pass for {req.event_name} {req.event_year}".strip(),
        "html": ticket_html(req, cid, event_code),
        "attachments": [{"filename": f"{safe_name}_QR.png", "content": list(png_bytes), "content_id": cid}],
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
        zip_bytes = strip_data_url(req.zip_base64)
    except Exception as e:
        raise HTTPException(400, f"Invalid zip_base64: {e}")
    params = {
        "from": FROM_HEADER,
        "to": [req.organizer_email],
        "subject": f"QR codes ZIP — {req.event_name} {req.event_year}".strip(),
        "html": zip_html(req),
        "attachments": [{"filename": req.zip_filename or "qr_codes.zip", "content": list(zip_bytes)}],
    }
    try:
        result = await asyncio.to_thread(resend.Emails.send, params)
        return {"status": "success", "email_id": result.get("id"), "to": req.organizer_email}
    except Exception as e:
        logger.error(f"send-zip-email failed: {e}")
        raise HTTPException(502, f"Resend error: {e}")

app.include_router(api_router)
