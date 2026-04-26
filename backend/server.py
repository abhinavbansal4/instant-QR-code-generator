from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import asyncio
import base64
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, EmailStr
from typing import List, Optional
import uuid
from datetime import datetime, timezone
import resend


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Resend setup
resend.api_key = os.environ.get('RESEND_API_KEY', '')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'onboarding@resend.dev')
SENDER_NAME = os.environ.get('SENDER_NAME', 'QR Events')
FROM_HEADER = f"{SENDER_NAME} <{SENDER_EMAIL}>"

app = FastAPI()
api_router = APIRouter(prefix="/api")


# --- Status models (kept) ---
class StatusCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StatusCheckCreate(BaseModel):
    client_name: str


# --- Email models ---
class SendQrEmailRequest(BaseModel):
    recipient_email: EmailStr
    recipient_name: str
    event_name: str
    event_year: Optional[str] = ''
    event_date: Optional[str] = ''
    event_venue: Optional[str] = ''
    qr_text: str
    qr_png_base64: str  # data URL or raw base64
    extra_note: Optional[str] = ''


class SendZipEmailRequest(BaseModel):
    organizer_email: EmailStr
    event_name: str
    event_year: Optional[str] = ''
    recipient_count: int = 0
    zip_base64: str
    zip_filename: str = 'qr_codes.zip'


def _strip_data_url(b64: str) -> bytes:
    """Accept either pure base64 or a data:...;base64,... URL."""
    if ',' in b64 and b64.lstrip().startswith('data:'):
        b64 = b64.split(',', 1)[1]
    return base64.b64decode(b64)


def _ticket_html(req: SendQrEmailRequest, cid: str) -> str:
    rows = []
    if req.event_date:
        rows.append(f"<tr><td style='padding:4px 0;color:#6b675e'>Date</td><td style='padding:4px 0 4px 16px'>{req.event_date}</td></tr>")
    if req.event_venue:
        rows.append(f"<tr><td style='padding:4px 0;color:#6b675e'>Venue</td><td style='padding:4px 0 4px 16px'>{req.event_venue}</td></tr>")
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


# --- Routes ---
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


@api_router.post("/send-qr-email")
async def send_qr_email(req: SendQrEmailRequest):
    if not resend.api_key:
        raise HTTPException(status_code=500, detail="Email not configured: missing RESEND_API_KEY")
    try:
        png_bytes = _strip_data_url(req.qr_png_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid qr_png_base64: {e}")

    cid = f"qr-{uuid.uuid4().hex[:8]}"
    safe_name = req.recipient_name.replace('/', '_').strip() or 'recipient'
    filename = f"{safe_name}_QR.png"

    params = {
        "from": FROM_HEADER,
        "to": [req.recipient_email],
        "subject": f"Your QR pass for {req.event_name} {req.event_year}".strip(),
        "html": _ticket_html(req, cid),
        "attachments": [
            {
                "filename": filename,
                "content": list(png_bytes),
                "content_id": cid,
            }
        ],
    }
    try:
        result = await asyncio.to_thread(resend.Emails.send, params)
        return {"status": "success", "email_id": result.get("id"), "to": req.recipient_email}
    except Exception as e:
        logger.error(f"send-qr-email failed: {e}")
        raise HTTPException(status_code=502, detail=f"Resend error: {e}")


@api_router.post("/send-zip-email")
async def send_zip_email(req: SendZipEmailRequest):
    if not resend.api_key:
        raise HTTPException(status_code=500, detail="Email not configured: missing RESEND_API_KEY")
    try:
        zip_bytes = _strip_data_url(req.zip_base64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid zip_base64: {e}")

    params = {
        "from": FROM_HEADER,
        "to": [req.organizer_email],
        "subject": f"QR codes ZIP — {req.event_name} {req.event_year}".strip(),
        "html": _zip_html(req),
        "attachments": [
            {
                "filename": req.zip_filename or "qr_codes.zip",
                "content": list(zip_bytes),
            }
        ],
    }
    try:
        result = await asyncio.to_thread(resend.Emails.send, params)
        return {"status": "success", "email_id": result.get("id"), "to": req.organizer_email}
    except Exception as e:
        logger.error(f"send-zip-email failed: {e}")
        raise HTTPException(status_code=502, detail=f"Resend error: {e}")


# --- Existing status routes (kept for compatibility) ---
@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.model_dump()
    status_obj = StatusCheck(**status_dict)
    doc = status_obj.model_dump()
    doc['timestamp'] = doc['timestamp'].isoformat()
    _ = await db.status_checks.insert_one(doc)
    return status_obj


@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    status_checks = await db.status_checks.find({}, {"_id": 0}).to_list(1000)
    for check in status_checks:
        if isinstance(check.get('timestamp'), str):
            check['timestamp'] = datetime.fromisoformat(check['timestamp'])
    return status_checks


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
