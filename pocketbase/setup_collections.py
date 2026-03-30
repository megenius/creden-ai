"""
Auto-setup PocketBase for Creden AI.
Runs inside the PocketBase container after startup.
Creates superuser + companies collection + imports data.
Uses only urllib (no requests library needed in Alpine).
"""
import os, json, re
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

PB_URL = f"http://127.0.0.1:{os.environ.get('PORT', '8090')}"
PB_EMAIL = os.environ.get('PB_EMAIL', 'admin@company.local')
PB_PASSWORD = os.environ.get('PB_PASSWORD', 'adminpassword1234')
DATA_FILE = "/app/_all_companies.json"


def api_request(method, path, data=None, token=None):
    """Simple urllib-based API helper."""
    url = f"{PB_URL}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, headers=headers, method=method)

    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()), resp.status
    except HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:
            body = {"error": str(e)}
        return body, e.code
    except Exception as e:
        return {"error": str(e)}, 0


def setup_superuser():
    """Create or authenticate superuser."""
    data, status = api_request("POST",
        "/api/collections/_superusers/auth-with-password",
        {"identity": PB_EMAIL, "password": PB_PASSWORD})

    if status == 200 and "token" in data:
        print("   ✅ Superuser authenticated")
        return data["token"]

    data, status = api_request("POST",
        "/api/collections/_superusers/records",
        {"email": PB_EMAIL, "password": PB_PASSWORD, "passwordConfirm": PB_PASSWORD})

    data, status = api_request("POST",
        "/api/collections/_superusers/auth-with-password",
        {"identity": PB_EMAIL, "password": PB_PASSWORD})

    if status == 200 and "token" in data:
        print("   ✅ Superuser created")
        return data["token"]

    print(f"   ⚠️  Superuser setup failed (status {status})")
    return None


def setup_companies_collection(token):
    """Create the companies collection with all fields."""
    _, status = api_request("GET", "/api/collections/companies", token=token)
    if status == 200:
        print("   ✅ Collection 'companies' already exists")
        return True

    text_fields = [
        {"name": "juristic_id", "type": "text", "required": True, "options": {"min": 13, "max": 13}},
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

    json_fields = [
        {"name": "directors", "type": "json", "required": False},
        {"name": "flags", "type": "json", "required": False},
    ]

    schema = {
        "name": "companies",
        "type": "base",
        "fields": text_fields + number_fields + json_fields,
        "indexes": ["CREATE UNIQUE INDEX idx_juristic_id ON companies (juristic_id)"],
        "listRule": "",
        "viewRule": "",
        "createRule": None,
        "updateRule": None,
        "deleteRule": None,
    }

    data, status = api_request("POST", "/api/collections", schema, token=token)
    if status == 200:
        print("   ✅ Collection 'companies' created")
        return True
    else:
        print(f"   ❌ Failed: {data}")
        return False


# ─── Thai number parsing ────────────────────────────────────────────────────

def parse_thai_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value).strip()
    if not value or value == '-' or value == 'N/A':
        return None
    text = value.replace(",", "")
    is_million = "ล้านบาท" in text
    is_baht = "บาท" in text and not is_million
    text = text.replace("ล้านบาท", "").replace("บาท", "").strip()
    try:
        num = float(text)
    except ValueError:
        return None
    if is_baht:
        return num / 1_000_000
    return num


def parse_ratio(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


FIELD_MAP = {
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
    "อัตราส่วนทุนหมุนเวียน (เท่า)": ("current_ratio", parse_ratio),
    "หนี้สินรวม/ส่วนของผู้ถือหุ้น (D/E) (เท่า)": ("de_ratio", parse_ratio),
    "อัตรากำไรสุทธิ (%)": ("net_profit_margin", parse_ratio),
    "อัตรากำไรขั้นต้น (%)": ("gross_profit_margin", parse_ratio),
    "ผลตอบแทนจากสินทรัพย์ ROA (%)": ("roa", parse_ratio),
    "ผลตอบแทนจากส่วนของผู้ถือหุ้น ROE (%)": ("roe", parse_ratio),
    "อัตราหมุนเวียนสินทรัพย์รวม (เท่า)": ("asset_turnover", parse_ratio),
    "กรรมการ": ("directors", None),
    "_FLAGS": ("flags", None),
}


def map_company_data(raw):
    data = raw.get('data', {})
    record = {}
    for json_key, (db_col, parser) in FIELD_MAP.items():
        value = data.get(json_key)
        if value is None:
            continue
        if parser is None:
            record[db_col] = value
        elif parser is str:
            record[db_col] = str(value)
        else:
            parsed = parser(value)
            if parsed is not None:
                record[db_col] = parsed
    return record


def import_companies(token):
    """Import company data from JSON file."""
    data, status = api_request("GET",
        "/api/collections/companies/records?perPage=1", token=token)
    if status == 200 and data.get("totalItems", 0) > 0:
        print(f"   ✅ Companies exist ({data['totalItems']} records)")
        return

    if not os.path.isfile(DATA_FILE):
        print(f"   ❌ Data file not found: {DATA_FILE}")
        return

    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        companies = json.load(f)

    count = 0
    for company in companies:
        record = map_company_data(company)
        if not record.get('juristic_id') or not record.get('name_th'):
            continue
        _, s = api_request("POST", "/api/collections/companies/records", record, token=token)
        if s == 200:
            count += 1
            print(f"   ✅ Imported: {record.get('name_th')}")
        else:
            print(f"   ❌ Failed: {record.get('name_th')}")

    print(f"   ✅ Imported {count}/{len(companies)} companies")


if __name__ == "__main__":
    token = setup_superuser()
    if token:
        setup_companies_collection(token)
        import_companies(token)
    print("   🎉 PocketBase auto-setup done")
