# QR Events — Generate · Deliver · Check-in

A complete event check-in toolkit built as a static-first web app: generate QR codes for any number of attendees, deliver them by email or print, and run a multi-device check-in at the door with a live TV dashboard.

> **One sentence**: Upload an Excel/CSV of attendees → save the event to the cloud → share a code → staff scan personal QRs at one door while attendees self-check-in via a poster QR at another → watch a live dashboard on a TV.

---

## Table of contents

1. [Highlights](#highlights)
2. [Architecture overview](#architecture-overview)
3. [Pages](#pages)
4. [Quick start](#quick-start)
5. [Step-by-step user guide](#step-by-step-user-guide)
6. [How check-in scenarios compare](#how-check-in-scenarios-compare)
7. [Multi-device safety & concurrency](#multi-device-safety--concurrency)
8. [Backend API reference](#backend-api-reference)
9. [Data model](#data-model)
10. [Tech stack](#tech-stack)
11. [Project layout](#project-layout)
12. [Configuration & environment variables](#configuration--environment-variables)
13. [Running locally](#running-locally)
14. [Production notes](#production-notes)
15. [Roadmap](#roadmap)

---

## Highlights

- **Bulk + single QR generation** — Excel (`.xlsx`/`.xls`) or CSV input, or one-off form. Optional center logo on every QR.
- **Multiple delivery channels** — Download ZIP, individual download, **email each pass** to its recipient (Resend), email the whole ZIP to the organizer, **print as A4 1-per-page or 4×2 badge sheet**, **print event poster** with self-check-in QR.
- **Live check-in (multi-device)** — staff can scan with a tablet on `/scan.html` while attendees self-check-in via `/checkin.html`. Both write to MongoDB; the same person can never be checked in twice.
- **Walk-in registration** — saffron `+ Add walk-in` button on the scanner page registers + checks-in + emails a pass in one tap.
- **Live TV dashboard** — `/dashboard.html` shows big animated counters, progress bar, check-ins/min, fresh-row-highlighted feed, auto-refreshing every 2-3 seconds.
- **Multi-event** — every API call is scoped by an 8-char `event_code` (e.g. `A4F9-X7K2`). Run as many events in parallel as you want with zero collision.
- **Persistent across reloads, browser shutdowns, devices** — all server state in MongoDB; localStorage caches the last code on each page for one-tap reconnect.

---

## Architecture overview

```
┌────────────────────────────────────────────────────────────────────┐
│                          BROWSER (any device)                      │
│                                                                    │
│  /                /scan.html         /dashboard.html  /checkin.html│
│  Generator        Staff scanner      Live TV dash     Self check-in│
│  (vanilla JS)     (vanilla JS)       (vanilla JS)     (vanilla JS) │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
                            ▲ /api/* (JSON)
                            │
┌────────────────────────────────────────────────────────────────────┐
│                       FASTAPI BACKEND (port 8001)                  │
│                                                                    │
│  /api/events/...        /api/send-qr-email     /api/send-zip-email │
│  events · recipients ·  Resend SDK             Resend SDK          │
│  checkins · walkin                                                 │
└────────────────────────────────────────────────────────────────────┘
                            ▲ Motor (async)
                            │
┌────────────────────────────────────────────────────────────────────┐
│                         MONGODB                                    │
│  events · event_recipients · event_checkins                        │
│  Unique compound index on (event_code, recipient_id) → atomic dedup│
└────────────────────────────────────────────────────────────────────┘
```

**No build step. No bundler.** All four frontend pages are static HTML files served from `frontend/public/`. CDN libraries: SheetJS (Excel parsing), JSZip (zip building), qr-code-styling (QR rendering), jsQR (camera decoding).

---

## Pages

| Path | Audience | What it does |
|---|---|---|
| `/` | Organizer | Generate QRs (bulk/single), embed logo, download/email/print, save to cloud |
| `/scan.html` | Staff at the door | Scan attendee QRs with camera, walk-in registration, print poster, live stats |
| `/dashboard.html` | Anyone (TV/projector) | Live counters + activity feed, auto-refresh |
| `/checkin.html` | Attendees on their own phone | Self check-in by scanning their QR or finding themselves by email/name |

---

## Quick start

1. Open `/` (Generator).
2. Type **Event name** + **Year** (e.g. *Guru Vandana 2025*).
3. (Optional) Upload a center logo.
4. **Bulk upload** tab → drop an Excel/CSV with a `name` column (and optional `email`). Or click **Load sample data** to demo.
5. Click **Generate QR codes** → grid of QR PNGs renders.
6. Click **☁️ Save event to cloud** → you get a code like `A4F9-X7K2` and two share buttons:
   - **📷 Open scanner** → `/scan.html?event=A4F9-X7K2`
   - **📊 Open dashboard** → `/dashboard.html?event=A4F9-X7K2`
7. (Optional) On scanner page, click **🪧 Print self-check-in poster** to print an A4 sheet attendees can scan to self check-in via `/checkin.html?event=A4F9-X7K2`.
8. At event time, both the staff scanner and attendees with the poster can check in. Watch the dashboard fill up.

---

## Step-by-step user guide

### A. Generator (`/`)

1. **Event setup** — Event name, year, date, venue, organizer email (used to send the bulk ZIP), QR size.
2. **QR text template** — what each QR encodes. Defaults to `{event_name} {year} - {name}`. Placeholders: `{event_name}` `{year}` `{name}` `{email}` `{f1}` `{f2}` (custom fields).
3. **Custom field labels** — give two extra columns names (e.g. `Department`, `Role`). Their column values then fill `{f1}`/`{f2}`.
4. **Logo upload** — PNG/JPG/SVG/WebP. The app auto-pads it with a rounded white plate and embeds it at 18% size with high error-correction so the QR stays scannable.
5. **Live preview** — shows the exact QR text and a rendered QR. The hint text under the label clarifies whether you're seeing a sample or your first real recipient.
6. **Cloud sync card** — saves the event + recipients to MongoDB, returns a code, and exposes share links to scanner & dashboard. Without saving, recipients live only in this browser. After saving, any local change auto-syncs to cloud.
7. **Add recipients** card has 3 tabs:
   - **Bulk upload** — drop xlsx/xls/csv. Required header: `name` (case-insensitive; aliases `recipient`, `teacher`, `teacher_name`, `full name`). Optional: `email`/`mail`. Custom fields by their label.
   - **Single QR** — quick form: Name (required), Email, Field 1, Field 2 → "Generate QR".
   - **Check-in** — local-only check-in scanner (saved in browser localStorage). For multi-device check-in use `/scan.html`.
8. **Generate & deliver** card actions:
   - **Generate QR codes** — renders all PNGs locally with optional logo.
   - **Download ZIP** — one zip with all PNGs, filenames auto-deduped with email local-part if names collide.
   - **Email ZIP to organizer** — uses the email entered in Setup.
   - **Print all (badges)** — 4×2 = 8 badges per A4 page.
   - **Print 1 per page** — large single-QR per A4 page.
   - **Per-card actions** — Download · Email · Print for each individual.

### B. Staff scanner (`/scan.html`)

- Either pass the event code in the URL (`?event=CODE`) or type it in the connect card.
- **Start camera** for live scanning, or paste QR text into **Manual entry**.
- A confirmation modal appears for each scan: **Welcome** (new), **Already checked in** (warning), or **Not found** (error).
- People-count stepper handles group entries.
- **+ Add walk-in** opens a modal: Name (required), Email (optional), people count, "Email a QR pass" checkbox. Backend atomically creates the recipient + check-in; the success modal renders the live QR and emails the pass if the box is checked.
- **🪧 Print self-check-in poster** prints an A4 with a 480×480 QR pointing at `/checkin.html?event=CODE`.
- **Export attendance CSV** — full list with timestamps, people count, status.

### C. Live dashboard (`/dashboard.html`)

- Big animated counters: Total · Checked in · Pending · Total people (group-aware).
- Progress bar with % checked in.
- Check-ins/min rate calculated over a sliding 5-min window.
- Live activity feed with avatar gradients, fresh rows highlighted in green.
- Auto-polls `/stats` every 2 s and `/checkins?limit=20` every 3 s.
- Throw on a TV/projector at the venue.

### D. Self check-in (`/checkin.html`)

- Mobile-first dark UI.
- Two paths:
  - **Scan my personal QR** — phone camera with jsQR.
  - **Find me by email or name** — case-insensitive search; if multiple match, a list lets the attendee tap their entry.
- Confirm card with people-count stepper.
- Animated confetti success overlay + haptic vibration.
- If the attendee was already checked in (any device, any path), the page tells them so instead of letting them double-check-in.

---

## How check-in scenarios compare

You can run **both at the same event** — same MongoDB, same dashboard, same atomic dedup.

| | Staff-scan (`/scan.html`) | Self check-in (`/checkin.html`) |
|---|---|---|
| **Who scans** | Staff scans attendee's personal QR | Attendee scans an event poster QR with their own phone |
| **Speed** | ~3-5 sec / person, serial | Parallel — many people at once |
| **Devices** | 1 staff tablet/phone per door | Each attendee uses their own phone |
| **Accuracy** | Highest (staff verifies) | Same — backend dedup prevents fraud across paths |
| **Group entries** | Easy +/− counter | Same |
| **Best for** | Older audiences, formal events, accuracy | High-volume rush, multiple doors, tech-savvy crowd |

Recommended: poster lane for self-service + tablet lane for those who need help.

---

## Multi-device safety & concurrency

When two staff scan the same person at the *exact same millisecond* on different devices:

1. Both POST `/api/events/{code}/checkin` simultaneously.
2. MongoDB has a **unique compound index on `(event_code, recipient_id)`** in `event_checkins`.
3. The first insert wins (returns `200 + checkin doc`).
4. The second insert raises `DuplicateKeyError` → backend returns **`409 Conflict`** with the existing check-in details.
5. The losing client UI shows *"Already checked in by another device"*.

**Verified live**: 2 simultaneous POSTs returned `{a: 200, b: 409}` — atomic, no duplicates, no lost data.

The same guarantee applies across **walk-in / self check-in / staff scan** — all three paths write through the same unique index.

---

## Backend API reference

Base URL: `${REACT_APP_BACKEND_URL}/api` (typically same origin via ingress, so just `/api/...`).

### Events

| Method | Path | Body | Description |
|---|---|---|---|
| `POST` | `/events` | `{name, year, date, venue, organizer_email, qr_template, qr_size, f1_label, f2_label}` | Create event, returns generated `event_code` |
| `GET`  | `/events/{code}` | — | Event meta + stats |
| `PUT`  | `/events/{code}` | event meta | Update meta |
| `DELETE` | `/events/{code}` | — | Delete event + all recipients + check-ins |
| `GET`  | `/events/{code}/stats` | — | `{total, checked_in, pending, people}` |

### Recipients

| Method | Path | Body | Description |
|---|---|---|---|
| `POST` | `/events/{code}/recipients/bulk` | `{recipients:[...], replace:true}` | Replace-all bulk upload |
| `GET`  | `/events/{code}/recipients` | — | List all |
| `POST` | `/events/{code}/lookup` | `{qr_text \| email \| name}` | Single match lookup with current check-in state |
| `POST` | `/events/{code}/lookup-multi` | `{email \| name}` | Up to 10 fuzzy matches with check-in state (for self check-in disambiguation) |

### Check-ins

| Method | Path | Body | Description |
|---|---|---|---|
| `POST` | `/events/{code}/checkin` | `{recipient_id, count}` | Atomic insert. **`409`** if already checked in |
| `POST` | `/events/{code}/walkin` | `{name, email, f1, f2, count}` | Create recipient + check-in atomically (one trip) |
| `GET`  | `/events/{code}/checkins?limit=N` | — | Recent check-ins newest-first |
| `DELETE` | `/events/{code}/checkins?confirm=YES` | — | Reset all check-ins for the event |

### Email

| Method | Path | Body | Description |
|---|---|---|---|
| `POST` | `/send-qr-email?event_code=…` | `{recipient_email, recipient_name, event_name, event_year, event_date, event_venue, qr_text, qr_png_base64, extra_note}` | HTML "ticket" body + PNG attachment via Resend |
| `POST` | `/send-zip-email` | `{organizer_email, event_name, event_year, recipient_count, zip_base64, zip_filename}` | ZIP attachment via Resend |
| `GET`  | `/email/health` | — | Sender config status |

---

## Data model

```
events                         {event_code (unique), name, year, date, venue,
                                organizer_email, qr_template, qr_size,
                                f1_label, f2_label, created_at, updated_at}

event_recipients               {event_code, recipient_id (unique with event_code),
                                name, email, f1, f2, qr_text, created_at,
                                is_walkin?}

event_checkins                 {event_code, recipient_id (UNIQUE with event_code),
                                name, email, qr_text, count, at,
                                is_walkin?}

# Indexes
events:           {event_code: 1}                                  unique
event_recipients: {event_code: 1, recipient_id: 1}                 unique
event_checkins:   {event_code: 1, recipient_id: 1}                 unique  ← race-safe
event_checkins:   {event_code: 1, at: -1}                          (feed query)
```

---

## Tech stack

- **Frontend**: 4 standalone HTML files, vanilla JS only. CDN libraries:
  - [SheetJS](https://sheetjs.com) — Excel/CSV parsing
  - [JSZip](https://stuk.github.io/jszip/) — client-side ZIP creation
  - [qr-code-styling](https://qr-code-styling.com) — QR rendering with embedded logo
  - [jsQR](https://github.com/cozmo/jsQR) — camera-based QR decoding
- **Backend**: FastAPI · Motor (async MongoDB) · Pydantic v2 · Resend SDK
- **Database**: MongoDB (works with local install or Atlas free tier — no code change)
- **Email**: Resend (3000 free emails/month)

---

## Project layout

```
/app
├── backend
│   ├── server.py          ← FastAPI app: events, recipients, checkins, email, walkin
│   ├── requirements.txt
│   └── .env               ← MONGO_URL, DB_NAME, RESEND_API_KEY, SENDER_*
├── frontend
│   ├── public
│   │   ├── index.html     ← Generator
│   │   ├── scan.html      ← Staff scanner
│   │   ├── dashboard.html ← Live TV dashboard
│   │   └── checkin.html   ← Self check-in
│   ├── src                ← (CRA shell — not used by the static pages, but keeps dev server)
│   ├── package.json
│   └── .env               ← REACT_APP_BACKEND_URL
└── README.md              ← (this file)
```

---

## Configuration & environment variables

### `backend/.env`

```env
MONGO_URL=mongodb://localhost:27017
DB_NAME=qr_events
CORS_ORIGINS=*
RESEND_API_KEY=re_...                  # https://resend.com → API Keys
SENDER_EMAIL=onboarding@resend.dev     # or events@yourdomain.com once verified
SENDER_NAME=QR Events
```

### `frontend/.env`

```env
REACT_APP_BACKEND_URL=https://your-host.example.com
```

> **Resend test mode**: with the default sender `onboarding@resend.dev`, Resend only delivers to the API-key owner's verified email. To send to anyone, verify a domain at https://resend.com/domains and set `SENDER_EMAIL=events@yourdomain.com`. **No code change needed**.

---

## Running locally

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8001 --reload

# Frontend (any static server is fine because pages are pure HTML)
cd frontend
yarn install
yarn start
```

Open http://localhost:3000 and you're off.

> If you change `.env` files, restart the corresponding service.

---

## Production notes

- **Atlas free tier** works as-is. Just point `MONGO_URL` at your cluster's connection string.
- **HTTPS is required** for the camera-based scanner pages (`/scan.html`, `/checkin.html`) on most modern browsers.
- The 4 frontend pages are pure static HTML — host them on any CDN/static host (Vercel, Netlify, S3+CloudFront).
- Backend can be deployed to any Python host (Render, Fly, Railway, etc.). Set the env vars and it works.
- Recommended: put backend behind the same domain (e.g. `/api/*`) so the frontend `fetch('/api/...')` calls just work without CORS.

---

## Roadmap

- [ ] Verify Resend domain — unblocks emails to anyone.
- [ ] Visual pills on dashboard differentiating staff scan / walk-in / self check-in.
- [ ] Server-Sent Events for instant dashboard updates (replace polling).
- [ ] Per-event admin/owner auth so strangers can't reset another event.
- [ ] Bulk recipient append (vs. replace) for multi-batch loading.
- [ ] Multi-language email templates.
- [ ] Direct folder write via File System Access API (Chrome/Edge) as an alternative to ZIP.
- [ ] Manual dark-mode toggle on the generator (today auto-follows OS).

---

Made with care for events of any size — from a 30-teacher cultural gathering to a 5,000-attendee conference.
