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

## Backlog / Next
- P1: Drag a folder containing many sheets (multi-file batch)
- P1: Custom output filename pattern (e.g., `{year}_{name}.png`)
- P2: Optional: Manual dark-mode toggle button
- P2: Optional logo overlay on each QR
- P2: Direct folder write via File System Access API (Chrome/Edge)
