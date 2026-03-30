"""
Auto-setup PocketBase: create superuser + companies collection + import data + embeddings.
Run after PocketBase starts for the first time.

Usage: python setup_db.py
"""
import os, sys, time, json, re, requests

PB_URL = os.environ.get('POCKETBASE_URL', 'http://127.0.0.1:8090')
PB_EMAIL = os.environ.get('PB_EMAIL', 'admin@company.local')
PB_PASSWORD = os.environ.get('PB_PASSWORD', 'adminpassword1234')

# ─── Data path ──────────────────────────────────────────────────────────────

DATA_FILE = os.path.join(os.path.dirname(__file__), '..', 'output', '_all_companies.json')


def wait_for_pb(max_wait=30):
    """Wait until PocketBase is ready."""
    for i in range(max_wait):
        try:
            resp = requests.get(f'{PB_URL}/api/health', timeout=2)
            if resp.ok:
                return True
        except Exception:
            pass
        time.sleep(1)
        print(f"   ⏳ Waiting for PocketBase... ({i+1}s)")
    return False


def create_superuser():
    """Create the initial superuser account."""
    try:
        resp = requests.post(f'{PB_URL}/api/collections/_superusers/auth-with-password',
                             json={'identity': PB_EMAIL, 'password': PB_PASSWORD}, timeout=10)
        if resp.ok:
            print("   ✅ Superuser already exists")
            return resp.json()['token']

        resp = requests.post(f'{PB_URL}/api/collections/_superusers/records',
                             json={'email': PB_EMAIL, 'password': PB_PASSWORD,
                                   'passwordConfirm': PB_PASSWORD}, timeout=10)
        if resp.ok or resp.status_code == 400:
            resp2 = requests.post(f'{PB_URL}/api/collections/_superusers/auth-with-password',
                                  json={'identity': PB_EMAIL, 'password': PB_PASSWORD}, timeout=10)
            if resp2.ok:
                print("   ✅ Superuser created")
                return resp2.json()['token']

        print(f"   ⚠️  Superuser setup returned: {resp.status_code}")
        print(f"       Open {PB_URL}/_/ in browser to create admin manually")
        return None
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return None


def create_companies_collection(token):
    """Create the 'companies' collection with all required fields."""
    headers = {'Authorization': f'Bearer {token}'}

    resp = requests.get(f'{PB_URL}/api/collections/companies', headers=headers, timeout=10)
    if resp.ok:
        print("   ✅ Collection 'companies' already exists")
        return True

    # Text fields
    text_fields = [
        {"name": "juristic_id", "type": "text", "required": True,
         "options": {"min": 13, "max": 13}},
        {"name": "name_th", "type": "text", "required": True, "options": {"max": 500}},
        {"name": "name_en", "type": "text", "required": False, "options": {"max": 500}},
        {"name": "entity_type", "type": "text", "required": False, "options": {"max": 200}},
        {"name": "status", "type": "text", "required": False, "options": {"max": 200}},
        {"name": "business_type", "type": "text", "required": False, "options": {"max": 500}},
        {"name": "business_category", "type": "text", "required": False, "options": {"max": 500}},
        {"name": "objective", "type": "text", "required": False, "options": {"max": 2000}},
        {"name": "tsic_code", "type": "text", "required": False, "options": {"max": 20}},
        {"name": "province", "type": "text", "required": False, "options": {"max": 100}},
        {"name": "region", "type": "text", "required": False, "options": {"max": 100}},
        {"name": "address", "type": "text", "required": False, "options": {"max": 2000}},
        {"name": "phone", "type": "text", "required": False, "options": {"max": 100}},
        {"name": "company_size", "type": "text", "required": False, "options": {"max": 100}},
        {"name": "reg_date_display", "type": "text", "required": False, "options": {"max": 200}},
        {"name": "fiscal_year_display", "type": "text", "required": False, "options": {"max": 200}},
        {"name": "signing_conditions", "type": "text", "required": False, "options": {"max": 10000}},
    ]

    # Number fields
    number_fields = [
        {"name": n, "type": "number", "required": False}
        for n in [
            "registered_capital", "total_assets", "total_liabilities", "equity",
            "total_revenue", "main_revenue", "net_profit", "gross_profit",
            "cost_of_sales", "total_expenses", "current_assets", "current_liabilities",
            "current_ratio", "de_ratio", "net_profit_margin", "gross_profit_margin",
            "roa", "roe", "asset_turnover",
        ]
    ]

    # JSON fields
    json_fields = [
        {"name": "directors", "type": "json", "required": False},
        {"name": "flags", "type": "json", "required": False},
    ]

    schema = {
        "name": "companies",
        "type": "base",
        "fields": text_fields + number_fields + json_fields,
        "indexes": [
            "CREATE UNIQUE INDEX idx_juristic_id ON companies (juristic_id)",
        ],
        # Public read-only API rules — for workshop simplicity
        "listRule": "",
        "viewRule": "",
        "createRule": None,
        "updateRule": None,
        "deleteRule": None,
    }

    resp = requests.post(f'{PB_URL}/api/collections', json=schema, headers=headers, timeout=10)
    if resp.ok:
        print("   ✅ Collection 'companies' created (17 text + 19 number + 2 json fields)")
        return True
    else:
        print(f"   ❌ Failed to create collection: {resp.status_code}")
        print(f"      {resp.text[:300]}")
        return False


# ─── Thai number parsing ────────────────────────────────────────────────────

def parse_thai_number(value) -> float | None:
    """แปลง '15,600 ล้านบาท' → 15600.0, '-95.6 ล้านบาท' → -95.6, '0 บาท' → 0"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    value = str(value).strip()
    if not value or value == '-' or value == 'N/A':
        return None

    # Remove commas
    text = value.replace(",", "")

    is_million = "ล้านบาท" in text
    is_baht = "บาท" in text and not is_million

    # Remove unit text
    text = text.replace("ล้านบาท", "").replace("บาท", "").strip()

    try:
        num = float(text)
    except ValueError:
        return None

    # ถ้าหน่วยเป็น "บาท" (ไม่ใช่ล้านบาท) → หาร 1,000,000
    if is_baht:
        return num / 1_000_000

    return num


def parse_ratio(value) -> float | None:
    """Parse ratio/percentage values — ไม่ต้องแปลงหน่วย ใช้ float ตรงๆ"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


# ─── Field mapping ──────────────────────────────────────────────────────────

# JSON key → (db column, parser)
FIELD_MAP = {
    # Text fields
    "เลขนิติบุคคล": ("juristic_id", str),
    "ชื่อบริษัท (ไทย)": ("name_th", str),
    "ชื่อบริษัท (อังกฤษ)": ("name_en", str),
    "ประเภทนิติบุคคล": ("entity_type", str),
    "สถานะ": ("status", str),
    "ประเภทธุรกิจ": ("business_type", str),
    "หมวดธุรกิจ": ("business_category", str),
    "วัตถุประสงค์": ("objective", str),
    "รหัส TSIC": ("tsic_code", str),
    "จังหวัด": ("province", str),
    "ภูมิภาค": ("region", str),
    "ที่อยู่": ("address", str),
    "โทรศัพท์": ("phone", str),
    "COMPANY_SIZE": ("company_size", str),
    "REG_DATE_DISPLAY": ("reg_date_display", str),
    "FISCAL_YEAR_DISPLAY": ("fiscal_year_display", str),
    "เงื่อนไขอำนาจลงนาม": ("signing_conditions", str),

    # Number fields (currency — ล้านบาท/บาท)
    "ทุนจดทะเบียน": ("registered_capital", parse_thai_number),
    "สินทรัพย์รวม": ("total_assets", parse_thai_number),
    "หนี้สินรวม": ("total_liabilities", parse_thai_number),
    "ส่วนของผู้ถือหุ้น": ("equity", parse_thai_number),
    "รายได้รวม": ("total_revenue", parse_thai_number),
    "รายได้หลัก": ("main_revenue", parse_thai_number),
    "กำไร(ขาดทุน)สุทธิ": ("net_profit", parse_thai_number),
    "กำไร(ขาดทุน)ขั้นต้น": ("gross_profit", parse_thai_number),
    "ต้นทุนขาย": ("cost_of_sales", parse_thai_number),
    "รายจ่ายรวม": ("total_expenses", parse_thai_number),
    "สินทรัพย์หมุนเวียน": ("current_assets", parse_thai_number),
    "หนี้สินหมุนเวียน": ("current_liabilities", parse_thai_number),

    # Number fields (ratios — ใช้ float ตรงๆ)
    "อัตราส่วนทุนหมุนเวียน (เท่า)": ("current_ratio", parse_ratio),
    "หนี้สินรวม/ส่วนของผู้ถือหุ้น (D/E) (เท่า)": ("de_ratio", parse_ratio),
    "อัตรากำไรสุทธิ (%)": ("net_profit_margin", parse_ratio),
    "อัตรากำไรขั้นต้น (%)": ("gross_profit_margin", parse_ratio),
    "ผลตอบแทนจากสินทรัพย์ ROA (%)": ("roa", parse_ratio),
    "ผลตอบแทนจากส่วนของผู้ถือหุ้น ROE (%)": ("roe", parse_ratio),
    "อัตราหมุนเวียนสินทรัพย์รวม (เท่า)": ("asset_turnover", parse_ratio),

    # JSON fields
    "กรรมการ": ("directors", None),  # Already a list
    "_FLAGS": ("flags", None),        # Already a list
}


def map_company_data(raw: dict) -> dict:
    """Map raw JSON company data to PocketBase record fields."""
    data = raw.get('data', {})
    record = {}

    for json_key, (db_col, parser) in FIELD_MAP.items():
        value = data.get(json_key)
        if value is None:
            continue

        if parser is None:
            # JSON fields — pass through
            record[db_col] = value
        elif parser is str:
            record[db_col] = str(value)
        else:
            parsed = parser(value)
            if parsed is not None:
                record[db_col] = parsed

    return record


# ─── Import ─────────────────────────────────────────────────────────────────

def import_companies(token):
    """Import companies from _all_companies.json into PocketBase."""
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    # Check if data already imported
    resp = requests.get(f'{PB_URL}/api/collections/companies/records?perPage=1',
                        headers=headers, timeout=10)
    if resp.ok and resp.json().get('totalItems', 0) > 0:
        count = resp.json()['totalItems']
        print(f"   ✅ Companies already imported ({count} records)")
        return

    # Load data file
    if not os.path.isfile(DATA_FILE):
        print(f"   ❌ Data file not found: {DATA_FILE}")
        print("      Expected: output/_all_companies.json")
        return

    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        companies = json.load(f)

    created = 0
    for company in companies:
        record = map_company_data(company)
        if not record.get('juristic_id') or not record.get('name_th'):
            print(f"   ⚠️  Skipping company without juristic_id or name_th")
            continue

        resp = requests.post(f'{PB_URL}/api/collections/companies/records',
                             json=record, headers=headers, timeout=10)
        if resp.ok:
            created += 1
            print(f"   ✅ Imported: {record.get('name_th', 'unknown')}")
        else:
            print(f"   ❌ Failed: {record.get('name_th', 'unknown')} — {resp.status_code}: {resp.text[:100]}")

    print(f"   ✅ Imported {created}/{len(companies)} companies")


# ─── Embeddings ─────────────────────────────────────────────────────────────

def setup_embeddings(token):
    """Generate embeddings for all companies and insert into sqlite_vec."""
    from gemini_client import get_embedding, GEMINI_API_KEY
    from vec_search import create_vec_table, insert_embedding, get_db_path, is_available

    db_path = get_db_path()
    if not os.path.isfile(db_path):
        print("   ⚠️  PocketBase db not found — skipping embeddings")
        return

    if not create_vec_table(db_path):
        print("   ⚠️  sqlite_vec not available — skipping embeddings")
        return

    if not GEMINI_API_KEY:
        print("   ⚠️  GEMINI_API_KEY not set — using fallback embeddings (quality will be low)")

    from pb_client import pb_list_all as _pb_list_all
    companies = _pb_list_all('companies')
    if not companies:
        print("   ❌ No companies found for embedding")
        return
    for c in companies:
        # Build embedding text
        parts = [c.get('name_th', '')]
        if c.get('name_en'):
            parts[0] += f" ({c['name_en']})"
        if c.get('business_type'):
            parts.append(c['business_type'])
        if c.get('objective'):
            parts.append(c['objective'])
        if c.get('business_category'):
            parts.append(c['business_category'])
        if c.get('province'):
            parts.append(c['province'])

        text = " — ".join(parts)
        embedding = get_embedding(text)
        insert_embedding(db_path, c['id'], embedding)
        print(f"   ✅ Embedding: {c.get('name_th', 'unknown')}")

    print(f"   ✅ Created embeddings for {len(companies)} companies")


# ─── Main ───────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("🔧 Creden AI — Database Setup")
    print(f"   PocketBase: {PB_URL}")
    print()

    if not wait_for_pb():
        print("   ❌ PocketBase is not running!")
        print(f"   Start it first: ./pocketbase serve")
        sys.exit(1)

    print("   ✅ PocketBase is running")

    token = create_superuser()
    if not token:
        print("\n   ⚠️  Could not authenticate. Please create admin at:")
        print(f"   {PB_URL}/_/")
        sys.exit(1)

    create_companies_collection(token)
    import_companies(token)
    setup_embeddings(token)

    print()
    print("🎉 Database setup complete!")
    print(f"   Admin UI: {PB_URL}/_/")
    print(f"   API: {PB_URL}/api/collections/companies/records")
