# Creden AI — Thai Company Data Browser + MCP Agent

## Project Overview
A company data browser built with Flask + PocketBase (local) + Vanilla JS, with a local MCP Server for AI Agent integration.
Claude Code is the AI agent — it uses MCP tools to query Thai company data from DBD + financial statements.

## Architecture
```
creden-ai/
├── app.py              # Flask REST API server (port 8766)
├── mcp_server.py       # MCP Server for AI tools (local stdio, 10 tools)
├── pb_client.py        # PocketBase REST API client (shared by app + mcp)
├── setup_db.py         # Auto-create collection + import data + embeddings
├── gemini_client.py    # Gemini embedding API (for sqlite_vec, fallback if no key)
├── vec_search.py       # sqlite_vec vector search helper
├── ui/                 # Frontend (Vanilla JS)
│   ├── index.html
│   ├── app.js
│   └── style.css
├── requirements.txt
├── start.sh            # Local dev: auto-download PocketBase + start everything
└── CLAUDE.md           # This file
```

## Tech Stack
- **Backend**: Python 3.12 + Flask
- **Database**: PocketBase (local binary, auto-downloaded by start.sh)
- **Vector Search**: sqlite_vec (auto-downloaded by start.sh)
- **Embeddings**: Google Gemini API (optional — fallback if no API key)
- **Frontend**: Vanilla JavaScript + HTML + CSS
- **AI Integration**: MCP (Model Context Protocol) via FastMCP — local stdio transport

## Quick Start (Local)
```bash
chmod +x start.sh && ./start.sh
# This will:
# 1. Download PocketBase binary (first run only)
# 2. Download sqlite_vec extension (first run only)
# 3. Create Python venv + install packages
# 4. Start PocketBase on port 8090
# 5. Auto-create companies collection + import 3 sample companies
# 6. Generate embeddings for vector search
# 7. Start Flask web server on port 8766
```

## How MCP Works Here
The MCP server runs **locally via stdio** — Claude Code launches it as a subprocess.
```bash
claude mcp add creden-ai -- python mcp_server.py
```
Then Claude Code can use all 10 MCP tools to query company data.

## Database (PocketBase — local)
PocketBase runs locally on port 8090. Admin UI: http://localhost:8090/_/

Collection: `companies` (37 fields)

### Text Fields (17)
- `juristic_id` (text, required, unique) — เลขนิติบุคคล 13 หลัก
- `name_th` (text, required) — ชื่อบริษัท (ไทย)
- `name_en` (text) — ชื่อบริษัท (อังกฤษ)
- `entity_type` — ประเภทนิติบุคคล
- `status` — สถานะ
- `business_type` — ประเภทธุรกิจ
- `business_category` — หมวดธุรกิจ
- `objective` — วัตถุประสงค์
- `tsic_code` — รหัส TSIC
- `province` — จังหวัด
- `region` — ภูมิภาค
- `address` — ที่อยู่เต็ม
- `phone` — โทรศัพท์
- `company_size` — ขนาดบริษัท
- `reg_date_display` — วันจดทะเบียน
- `fiscal_year_display` — ปีบัญชี
- `signing_conditions` — เงื่อนไขอำนาจลงนาม

### Number Fields (19) — units: ล้านบาท (unless noted)
- `registered_capital`, `total_assets`, `total_liabilities`, `equity`
- `total_revenue`, `main_revenue`, `net_profit`, `gross_profit`
- `cost_of_sales`, `total_expenses`, `current_assets`, `current_liabilities`
- `current_ratio` (เท่า), `de_ratio` (เท่า)
- `net_profit_margin` (%), `gross_profit_margin` (%)
- `roa` (%), `roe` (%), `asset_turnover` (เท่า)

### JSON Fields (2)
- `directors` — รายชื่อกรรมการ (array of strings)
- `flags` — คำเตือน/ข้อสังเกต (array of strings)

## API Endpoints (read-only)
- `GET  /api/companies` — List companies (query: province, company_size, sort, limit)
- `GET  /api/companies/search?q=` — Search by name/juristic_id/business_type
- `GET  /api/companies/:id` — Get single company
- `GET  /api/stats` — Aggregate statistics
- `GET  /health` — Health check

## MCP Tools (10 tools, local stdio)
1. `lookup_company` — ค้นหาบริษัทจากชื่อหรือเลขนิติบุคคล
2. `query_financials` — ค้นหาตาม filter ทางการเงิน (PocketBase filter syntax)
3. `compare_companies` — เปรียบเทียบหลายบริษัทพร้อมกัน
4. `search_similar` — ค้นหาบริษัทธุรกิจคล้ายกัน (vector search)
5. `list_directors` — ค้นหากรรมการ
6. `aggregate_stats` — คำนวณสถิติรวม (SUM/AVG/MAX/MIN/COUNT)
7. `get_data_summary` — ภาพรวมข้อมูลทั้งหมด
8. `get_financial_health` — วิเคราะห์สุขภาพการเงิน
9. `search_companies` — ค้นหาข้อความจากทุก field
10. `get_company_profile` — แสดงข้อมูลครบทุก field

## Environment Variables
- `POCKETBASE_URL` — PocketBase URL (default: http://127.0.0.1:8090)
- `PB_EMAIL` — PocketBase admin email (default: admin@company.local)
- `PB_PASSWORD` — PocketBase admin password (default: adminpassword1234)
- `GEMINI_API_KEY` — Google Gemini API key (optional — for vector search embeddings)
- `PORT` — Web server port (default: 8766)
- `ALLOWED_ORIGINS` — CORS origins (default: *)

## Data Traps (สิ่งที่ต้องระวัง)
1. **ส่วนของผู้ถือหุ้นติดลบ**: Lazada equity = -95.6 ล้าน → D/E, ROE ไม่สามารถตีความปกติ
2. **บริษัทบริการ**: Creden มี gross_profit = 0 เพราะเป็นบริษัทบริการ ไม่ใช่ไม่มีกำไร
3. **Scale ต่างกัน**: PTT OR รายได้ 737,535 ล้าน vs Creden 16.9 ล้าน → เปรียบเทียบด้วย % เท่านั้น
4. **ปีพุทธศักราช**: ถ้า user ถาม "งบปี 2023" → แปลงเป็น พ.ศ. 2566 ก่อนค้นหา
5. **กรรมการ ≠ ผู้ถือหุ้น**: ข้อมูลที่มีคือกรรมการเท่านั้น

## Sample Data (3 companies)
| บริษัท | เลขนิติบุคคล | สินทรัพย์ | รายได้ | กำไรสุทธิ | ขนาด |
|---|---|---|---|---|---|
| ลาซาด้า จำกัด | 0105555040244 | 11,610 ล้าน | 21,471 ล้าน | 604.6 ล้าน | ใหญ่ |
| ครีเดน เอเชีย จำกัด | 0105560098166 | 14.8 ล้าน | 16.9 ล้าน | 1.3 ล้าน | SME |
| ปตท. น้ำมันฯ (มหาชน) | 0107561000013 | 255,684 ล้าน | 737,535 ล้าน | 8,623 ล้าน | ใหญ่ |

## Coding Style
- Python: Follow existing patterns (Flask route decorators, pb_client helpers)
- JavaScript: Vanilla JS, no frameworks, async/await for API calls
- Keep functions small and focused
- Use Thai comments where helpful
