# Bulk QR Code Generator — PRD

## Original Problem Statement
Generic website that generates a QR code for every teacher, for any year. Takes an Excel/CSV sheet as input and produces QR PNGs named after each teacher.

## Architecture
- **Single self-contained HTML file** at `/app/frontend/public/index.html`
- **Vanilla JavaScript** (no React, no build step required)
- **CDN libraries**: SheetJS (xlsx 0.18.5) for Excel/CSV parsing, JSZip (3.10.1) for bundling outputs
- **QR generation**: GET requests to `https://api.qrserver.com/v1/create-qr-code/`
- Served by the existing CRA dev server (CRA injects its bundle but does nothing because there is no `#root`)

## User Personas
- **Event organizer** — uploads a teacher list once a year, downloads a folder of QR PNGs to print/share

## Core Requirements (static)
- Configurable event name + year + QR size + QR text template (placeholders `{event_name}` `{year}` `{teacher_name}` `{email}`)
- File upload supporting `.xlsx`, `.xls`, `.csv` (drag-and-drop or click)
- Required column `name`; optional `email`; case-insensitive header lookup; aliases `teacher`, `teacher_name`, `mail`
- Live QR preview tied to template + event fields
- One ZIP download with all PNGs named `{Teacher_Name}_QR.png` (filename-sanitized, deduped)
- Per-teacher individual download
- Light + auto dark theme via `prefers-color-scheme`

## Implemented (2026-02)
- [x] Setup card: event name, year, QR size, QR template, live preview
- [x] Drag-and-drop + click upload for xlsx/xls/csv
- [x] Sample data loader, clear list
- [x] Stats: total / with email / generated / event label
- [x] Teachers preview table with computed QR text per row
- [x] Bulk QR fetch with progress bar
- [x] Per-teacher QR card grid with individual download links
- [x] ZIP download named `{event}_{year}_QR_Codes.zip`
- [x] **Center logo embedding** — upload PNG/JPG/SVG/WebP, auto-padded with rounded white plate, embedded at 18% size with ECC=H for safe scanning. Switched generation from api.qrserver.com to client-side `qr-code-styling`.
- [x] Stylized QR look — square dots, extra-rounded corner-square frames, saffron accent on corner dots
- [x] Validated end-to-end with logo (sample → 6 QRs with logo → ZIP downloaded)

## Update (2026-02 — generic + delivery)
- Renamed everything to **generic event QR generator** (recipients, not teachers); event-agnostic copy and defaults.
- Added **Mode tabs**: Bulk upload vs Single QR (quick form: name, email, 2 custom fields).
- **Custom field labels** in Setup; placeholders `{f1}`, `{f2}` usable in QR template; Excel/CSV columns are matched by these labels.
- **Duplicate-name handling** in ZIP filenames: appends email local-part to disambiguate (e.g., `Ravi_Iyer_ravi_QR.png` + `Ravi_Iyer_ravi.iyer2_QR.png`); falls back to `_2`, `_3` if still colliding.
- **Per-recipient actions**: Download · Email · Print.
- **Bulk actions**: Download ZIP · Email ZIP to organizer · Print all (badges 4×2/page) · Print 1-per-page.
- **Email integration**: Resend via FastAPI backend
  - `POST /api/send-qr-email` — HTML "ticket" body (event title, date, venue, QR inline) + PNG attachment.
  - `POST /api/send-zip-email` — ZIP attachment to organizer with summary HTML body.
  - `GET  /api/email/health` — sender/config status.
  - Sender: `onboarding@resend.dev` (test sender; in test mode Resend only delivers to the API-key owner's verified email; user verifies a domain at resend.com/domains for production sending).
- **Animated gradient-mesh background** on hero/landing + glass-morphism cards with backdrop blur.
- Print stylesheet via `@media print` — two layouts (A4 single + 4×2 badge sheet).
- E2E verified: 6-recipient sample (with duplicate name) → generated → ZIP filenames deduped → email to verified address returned Resend `email_id` ✓.

## Update (2026-02 — check-in scanner + landing polish)
- **New "Check-in" tab** with live attendance scanner:
  - Camera-based QR scanning via `jsQR` (rear camera by default, `getUserMedia`)
  - Manual paste fallback for keyboards / pre-printed QRs
  - Confirmation modal: Welcome / Already-checked-in / Not-found states
  - People-count stepper (+/-) per scan to support group entries
  - Live stats: total · checked-in · pending · total people
  - Recent check-ins table (newest first, with timestamps)
  - **Export attendance CSV** with full status (Name, Email, QR text, Checked-in at, People count, Status)
  - Reset all check-ins (with confirm)
- **localStorage persistence** — Setup fields, recipients list (incl. qrText) and check-ins survive page reloads. Auto-saved on every change.
- **Landing page overhaul**:
  - Saffron/amber gradient mesh background with animated drifting blobs (CSS `@property` + keyframes)
  - Bigger hero with gradient title ("check-in attendees" highlighted), pulsing "EVENT QR TOOLKIT" eyebrow badge, decorative QR-pattern SVG, slow-spinning conic gradient ring, glass-morphism card with white rim highlight
  - Feature pills: Bulk & single · Logo embedded · Email delivery · Print badges · Live check-in
- E2E verified end-to-end: scan known → confirm → counter+1; scan again → "Already checked in"; scan random → "Not found"; CSV downloaded with correct rows.

## Update (2026-02 — multi-page architecture + cloud)

### Three pages now (all served from `/app/frontend/public/`):
1. **`/`** — Generator (existing) + new **Cloud sync** card (Save / Re-sync / Load by code / Disconnect, with Open scanner & Open dashboard share-link buttons).
2. **`/scan.html`** — Standalone Scanner page (dark theme, large camera frame, manual paste, server-backed stats + recent activity, CSV export, reset). Connects via `?event=CODE` URL or input field. Polls stats every 2.5s, log every 4s.
3. **`/dashboard.html`** — Live Event Dashboard (TV-friendly). Big animated counters (easeOutCubic), progress bar with %, check-ins/min rate, live activity feed (last 20) with fresh-row highlighting, event details panel, decorative QR pattern. Polls stats every 2s, feed every 3s.

### Backend (FastAPI, MongoDB) — new collections + endpoints:
- `events` (unique index on `event_code`)
- `event_recipients` (unique index on `event_code+recipient_id`)
- `event_checkins` (**unique index on `event_code+recipient_id`**, secondary index on `at` desc) — race-safe atomic insert.

Endpoints:
- `POST /api/events` — generate 8-char code (dash-separated, no confusables), create event
- `GET/PUT/DELETE /api/events/{code}` — CRUD on event
- `GET /api/events/{code}/stats` — total / checked_in / pending / people (aggregation)
- `POST /api/events/{code}/recipients/bulk` — replace-all sync
- `GET /api/events/{code}/recipients` — list
- `POST /api/events/{code}/lookup` — find recipient by qr_text + current check-in
- `POST /api/events/{code}/checkin` — atomic insert; returns **409 + existing checkin** on duplicate-key (multi-device safe)
- `GET /api/events/{code}/checkins?limit=N` — recent first
- `DELETE /api/events/{code}/checkins?confirm=YES` — reset
- Email endpoints unchanged.

### Multi-event concurrency
- Every API call is scoped by `event_code` → unlimited events run in parallel without collision.
- Two staff scanning the same person at the same instant on different devices → DB allows only one insert; second gets **409 Already Checked In** with the existing check-in details.

### Persistence safety
- All cloud-event data is in MongoDB → survives browser shutdowns, refreshes, and device switches.
- All three pages cache the last `event_code` in localStorage for convenience (auto-reconnect on revisit).

### Tested end-to-end (2026-02)
- Generator → Save to cloud → code `3WPR-5NKV`
- Scanner auto-connect → 6/6 recipients loaded
- Cross-device: Device 1 checks in Anita (in=1) → Device 2 sees "Already checked in"
- Cross-device different person: Device 2 checks in Ravi → in=2
- Dashboard live: total=6, in=2, pending=4, 33%, 2 feed rows
- **Race test**: 2 concurrent POSTs for same recipient → `{a: 409, b: 200}` (atomic, exactly one succeeds)
- Dashboard auto-updated 2 → 3 after race ✓

## Backlog / Next
- P1: Verify domain in Resend so walk-in & ticket emails work for any address (currently locked to API-key owner).
- P1: Mark walk-ins visually in scanner log + dashboard feed (e.g., a small "WALK-IN" pill).
- P1: Server-Sent Events for dashboard (replace polling).
- P2: Bulk recipient append (vs replace), CSV export from generator after cloud-load.
- P2: Per-event admin/owner auth.
- P2: Manual dark-mode toggle, brand presets, multi-language email templates.

## Update (2026-02 — walk-in registration on scanner)
- New endpoint **`POST /api/events/{code}/walkin`** atomically creates a recipient + check-in (marked `is_walkin: true`), builds qr_text from event template.
- Scanner page got **`+ Add walk-in`** button (saffron) opening a modal: Name (required), Email (optional), People count, "Email a QR pass" checkbox.
- On submit: backend call → success modal renders the QR live (qr-code-styling added to scan.html) → optional email via existing `/api/send-qr-email` (with `event_code` for the ticket).
- Verified live: walk-in inserted, stats jumped `in 4→5 / total 7→8 / people 4→6` (group of 2), success QR shown, **email delivered** to verified Resend address ✓.
