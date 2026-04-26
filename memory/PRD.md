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

## Backlog / Next
- P1: Verify a domain in Resend so emails can go to anyone.
- P1: Multi-device check-in via backend sync (currently localStorage only — single device/browser).
- P2: Bundled default logo / brand presets per event, manual dark-mode toggle, multi-language email templates.
- P2: Beep / haptic feedback on successful scan.
- P2: Direct folder write via File System Access API.
