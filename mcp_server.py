"""
Creden AI — MCP Server
Exposes company data tools for AI assistants via Model Context Protocol.
Claude Code is the agent — these tools let it query Thai company data.

Architecture mirrors aiya-todo: FastMCP + pb_client pattern
"""
import os, sys, json
from mcp.server.fastmcp import FastMCP
from pb_client import pb_list, pb_list_all, pb_get

# ─── Config ──────────────────────────────────────────────────────────────────

MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MCP_PORT", os.environ.get("PORT", "8767")))

# ─── Create MCP Server ──────────────────────────────────────────────────────

mcp = FastMCP(
    "creden-ai",
    host=MCP_HOST,
    port=MCP_PORT,
    instructions="""Creden AI — ข้อมูลบริษัทไทยจาก DBD + งบการเงิน

คุณเป็น AI ที่ช่วยตอบคำถามเกี่ยวกับข้อมูลบริษัทไทย จากข้อมูลกรมพัฒนาธุรกิจการค้า (DBD) และงบการเงิน
ใช้ tools เหล่านี้เพื่อค้นหาข้อมูล — ห้ามตอบจากความจำ

## Tool Usage Instructions
- ถามเกี่ยวกับบริษัทเฉพาะ (ชื่อ/เลขนิติบุคคล) → ใช้ lookup_company
- ถามเปรียบเทียบหลายบริษัท → ใช้ compare_companies
- ถาม ranking/filter ทางการเงิน → ใช้ query_financials
- ถามแบบ open-ended / ค้นหาธุรกิจคล้ายกัน → ใช้ search_similar
- ถามเกี่ยวกับกรรมการ → ใช้ list_directors
- ถามสถิติรวม/ค่าเฉลี่ย → ใช้ aggregate_stats
- ต้องการ profile ครบ → ใช้ get_company_profile
- ต้องการวิเคราะห์สุขภาพการเงิน → ใช้ get_financial_health

## กฎสำคัญ
- เรียก tool ก่อนตอบเสมอ ห้ามตอบจากความจำ
- ถ้า tool ไม่พบข้อมูล ให้ตอบว่า "ไม่พบข้อมูลในระบบ" ห้ามเดา
- ข้อมูลใช้ระบบปีพุทธศักราช (พ.ศ.) — ถ้า user ถามเป็น ค.ศ. ให้แปลง: ค.ศ. + 543 = พ.ศ.
- ตอบทั้งสองระบบเสมอ เช่น "ปีบัญชี พ.ศ. 2566 (ค.ศ. 2023)"
- แสดงหน่วยเป็น "ล้านบาท" เสมอ
- ถ้าเปรียบเทียบบริษัทต่างขนาด ให้ใช้อัตราส่วน (%) ไม่ใช่ตัวเลขดิบ
- ถ้า equity ติดลบ ห้ามตีความ D/E, ROE ตามปกติ
- กำไรขั้นต้น = 0 ในบริษัทบริการไม่ได้หมายความว่าไม่มีกำไร
- กรรมการ ≠ ผู้ถือหุ้น"""
)

# ─── Helpers ────────────────────────────────────────────────────────────────

def fmt_currency(val):
    """Format number as Thai currency."""
    if val is None:
        return "N/A"
    if abs(val) >= 1000:
        return f"{val:,.0f} ล้านบาท"
    return f"{val:,.2f} ล้านบาท"

def fmt_ratio(val, unit="%"):
    """Format ratio/percentage."""
    if val is None:
        return "N/A"
    return f"{val:,.2f}{unit}"

def fmt_company_card(c, idx=None):
    """Format company as a one-line summary."""
    prefix = f"{idx}. " if idx else ""
    size = "🏢" if c.get('company_size') == 'บริษัทขนาดใหญ่' else "🏠"
    profit = c.get('net_profit')
    profit_str = f" | กำไร: {fmt_currency(profit)}" if profit is not None else ""
    return f"{prefix}{size} {c.get('name_th', 'N/A')} ({c.get('name_en', '')}) | รายได้: {fmt_currency(c.get('total_revenue'))}{profit_str}"

def fmt_company_detail(c):
    """Format full company details."""
    lines = [
        f"═══ {c.get('name_th', 'N/A')} ═══",
        f"ชื่ออังกฤษ: {c.get('name_en', 'N/A')}",
        f"เลขนิติบุคคล: {c.get('juristic_id', 'N/A')}",
        f"ประเภท: {c.get('entity_type', 'N/A')} | สถานะ: {c.get('status', 'N/A')}",
        f"ธุรกิจ: {c.get('business_type', 'N/A')}",
        f"หมวด: {c.get('business_category', 'N/A')}",
        f"วัตถุประสงค์: {c.get('objective', 'N/A')}",
        f"จังหวัด: {c.get('province', 'N/A')} ({c.get('region', 'N/A')})",
        f"ขนาด: {c.get('company_size', 'N/A')}",
        f"จดทะเบียน: {c.get('reg_date_display', 'N/A')}",
        f"ปีบัญชี: {c.get('fiscal_year_display', 'N/A')}",
        "",
        "─── งบการเงิน (ล้านบาท) ───",
        f"ทุนจดทะเบียน: {fmt_currency(c.get('registered_capital'))}",
        f"สินทรัพย์รวม: {fmt_currency(c.get('total_assets'))}",
        f"หนี้สินรวม: {fmt_currency(c.get('total_liabilities'))}",
        f"ส่วนของผู้ถือหุ้น: {fmt_currency(c.get('equity'))}",
        f"รายได้รวม: {fmt_currency(c.get('total_revenue'))}",
        f"รายได้หลัก: {fmt_currency(c.get('main_revenue'))}",
        f"กำไรขั้นต้น: {fmt_currency(c.get('gross_profit'))}",
        f"กำไรสุทธิ: {fmt_currency(c.get('net_profit'))}",
        "",
        "─── อัตราส่วนทางการเงิน ───",
        f"Current Ratio: {fmt_ratio(c.get('current_ratio'), ' เท่า')}",
        f"D/E Ratio: {fmt_ratio(c.get('de_ratio'), ' เท่า')}",
        f"ROA: {fmt_ratio(c.get('roa'))}",
        f"ROE: {fmt_ratio(c.get('roe'))}",
        f"Net Profit Margin: {fmt_ratio(c.get('net_profit_margin'))}",
        f"Gross Profit Margin: {fmt_ratio(c.get('gross_profit_margin'))}",
        f"Asset Turnover: {fmt_ratio(c.get('asset_turnover'), ' เท่า')}",
    ]

    directors = c.get('directors') or []
    if directors:
        lines.append(f"\n─── กรรมการ ({len(directors)} คน) ───")
        for d in directors:
            lines.append(f"  • {d}")

    flags = c.get('flags') or []
    if flags:
        lines.append("\n─── ⚠️ Flags ───")
        for f in flags:
            lines.append(f"  ⚠️ {f}")

    return "\n".join(lines)


def _lookup(query: str):
    """Internal: find companies by name or juristic_id."""
    q = query.strip()
    if q.replace(" ", "").isdigit() and len(q.replace(" ", "")) == 13:
        items = pb_list('companies', filter=f'juristic_id = "{q.replace(" ", "")}"', limit=5)
    else:
        items = pb_list('companies', filter=f'name_th ~ "{q}" || name_en ~ "{q}"', limit=10)
    return items


# ─── Tools: Core Query ─────────────────────────────────────────────────────

@mcp.tool()
async def lookup_company(query: str) -> str:
    """ค้นหาข้อมูลบริษัทจากชื่อ (ไทย/อังกฤษ) หรือเลขนิติบุคคล 13 หลัก

    Args:
        query: ชื่อบริษัท (ไทยหรืออังกฤษ) หรือเลขนิติบุคคล
    """
    items = _lookup(query)
    if not items:
        return f"ไม่พบข้อมูลบริษัทที่ตรงกับ '{query}'"

    if len(items) == 1:
        return fmt_company_detail(items[0])

    lines = [f"พบ {len(items)} บริษัทที่ตรงกับ '{query}':", ""]
    for i, c in enumerate(items, 1):
        lines.append(fmt_company_card(c, i))
    return "\n".join(lines)


@mcp.tool()
async def query_financials(filter: str, sort: str = "-net_profit",
                           limit: int = 10, fields: str = "") -> str:
    """ค้นหาบริษัทตาม filter ทางการเงิน ใช้ PocketBase filter syntax
    เช่น 'net_profit > 100 && province = "กรุงเทพมหานคร"'

    Args:
        filter: PocketBase filter syntax เช่น 'net_profit > 100'
        sort: เรียงตาม field เช่น '-net_profit' (- = descending)
        limit: จำนวนผลลัพธ์สูงสุด (default 10)
        fields: fields ที่ต้องการ คั่นด้วย comma (optional)
    """
    try:
        items = pb_list('companies', filter=filter, sort=sort, limit=limit)
    except Exception as e:
        return f"❌ Filter error: {e}\nHint: ใช้ PocketBase filter syntax เช่น 'net_profit > 0 && province = \"กรุงเทพมหานคร\"'"

    if not items:
        return f"ไม่พบบริษัทที่ตรงกับ filter: {filter}"

    lines = [f"พบ {len(items)} บริษัท (filter: {filter}, sort: {sort}):", ""]
    for i, c in enumerate(items, 1):
        lines.append(fmt_company_card(c, i))
    return "\n".join(lines)


@mcp.tool()
async def compare_companies(company_names: str, metrics: str = "") -> str:
    """เปรียบเทียบข้อมูลหลายบริษัทพร้อมกัน

    Args:
        company_names: ชื่อบริษัทคั่นด้วย comma เช่น "Lazada, Creden, PTT OR"
        metrics: metrics ที่ต้องการเปรียบเทียบ คั่นด้วย comma (optional, default = all key metrics)
    """
    names = [n.strip() for n in company_names.split(',') if n.strip()]
    if len(names) < 2:
        return "กรุณาระบุบริษัทอย่างน้อย 2 แห่ง คั่นด้วย comma"

    results = []
    not_found = []
    for name in names:
        items = _lookup(name)
        if items:
            results.append(items[0])
        else:
            not_found.append(name)

    if not results:
        return f"ไม่พบบริษัทใดเลย: {', '.join(not_found)}"

    # Default metrics
    if metrics:
        metric_keys = [m.strip() for m in metrics.split(',')]
    else:
        metric_keys = ['total_revenue', 'net_profit', 'total_assets', 'equity',
                        'roa', 'roe', 'de_ratio', 'net_profit_margin', 'current_ratio']

    metric_labels = {
        'total_revenue': 'รายได้รวม (ล้านบาท)',
        'net_profit': 'กำไรสุทธิ (ล้านบาท)',
        'total_assets': 'สินทรัพย์รวม (ล้านบาท)',
        'equity': 'ส่วนของผู้ถือหุ้น (ล้านบาท)',
        'registered_capital': 'ทุนจดทะเบียน (ล้านบาท)',
        'total_liabilities': 'หนี้สินรวม (ล้านบาท)',
        'roa': 'ROA (%)',
        'roe': 'ROE (%)',
        'de_ratio': 'D/E (เท่า)',
        'net_profit_margin': 'Net Profit Margin (%)',
        'gross_profit_margin': 'Gross Profit Margin (%)',
        'current_ratio': 'Current Ratio (เท่า)',
        'asset_turnover': 'Asset Turnover (เท่า)',
        'company_size': 'ขนาด',
        'province': 'จังหวัด',
    }

    # Build comparison table
    lines = [f"═══ เปรียบเทียบ {len(results)} บริษัท ═══", ""]

    # Header
    header = f"{'Metric':<30}"
    for c in results:
        name = c.get('name_th', 'N/A')[:15]
        header += f" | {name:>15}"
    lines.append(header)
    lines.append("─" * len(header))

    # Data rows
    for key in metric_keys:
        label = metric_labels.get(key, key)[:30]
        row = f"{label:<30}"
        for c in results:
            val = c.get(key)
            if isinstance(val, str):
                row += f" | {val:>15}"
            elif val is not None:
                row += f" | {val:>15,.2f}"
            else:
                row += f" | {'N/A':>15}"
        lines.append(row)

    if not_found:
        lines.append(f"\n⚠️ ไม่พบ: {', '.join(not_found)}")

    return "\n".join(lines)


@mcp.tool()
async def search_similar(query: str, top_k: int = 5) -> str:
    """ค้นหาบริษัทที่มีธุรกิจคล้ายกัน ใช้ semantic vector search
    เหมาะสำหรับคำถาม open-ended เช่น 'บริษัทที่ทำ e-commerce' หรือ 'บริษัทคล้าย Lazada'

    Args:
        query: คำอธิบายประเภทธุรกิจที่ต้องการค้นหา
        top_k: จำนวนผลลัพธ์ (default 5)
    """
    from gemini_client import get_embedding
    from vec_search import search_similar as vec_search, get_db_path, is_available

    if not is_available():
        # Fallback: text search via PocketBase
        items = pb_list('companies',
                        filter=f'business_type ~ "{query}" || objective ~ "{query}" || name_th ~ "{query}"',
                        limit=top_k)
        if not items:
            return f"ไม่พบบริษัทที่คล้ายกับ '{query}' (vector search ไม่พร้อมใช้งาน, ใช้ text search แทน)"

        lines = [f"พบ {len(items)} บริษัทจาก text search (vector search ไม่พร้อมใช้งาน):", ""]
        for i, c in enumerate(items, 1):
            lines.append(fmt_company_card(c, i))
        return "\n".join(lines)

    # Vector search
    embedding = get_embedding(query)
    results = vec_search(get_db_path(), embedding, top_k=top_k)

    if not results:
        return f"ไม่พบบริษัทที่คล้ายกับ '{query}'"

    found = []
    for r in results:
        company = pb_get('companies', r['company_id'])
        if company:
            found.append((company, r['distance']))

    if not found:
        return f"ไม่พบบริษัทที่คล้ายกับ '{query}'"

    lines = [f"พบ {len(found)} บริษัทที่คล้ายกับ '{query}' (semantic search):", ""]
    for i, (company, dist) in enumerate(found, 1):
        lines.append(f"{fmt_company_card(company, i)} (distance: {dist:.4f})")

    return "\n".join(lines)


@mcp.tool()
async def list_directors(query: str) -> str:
    """ค้นหากรรมการของบริษัท หรือค้นว่าบุคคลนี้เป็นกรรมการบริษัทไหนบ้าง

    Args:
        query: ชื่อบริษัท หรือ ชื่อกรรมการ
    """
    # Search by company name first
    items = _lookup(query)

    if items:
        # Found companies — show their directors
        lines = []
        for c in items:
            directors = c.get('directors') or []
            lines.append(f"═══ กรรมการ {c.get('name_th', 'N/A')} ({len(directors)} คน) ═══")
            for d in directors:
                lines.append(f"  • {d}")
            if c.get('signing_conditions'):
                lines.append(f"\nเงื่อนไขอำนาจลงนาม:")
                lines.append(f"  {c['signing_conditions'][:500]}")
            lines.append("")
        return "\n".join(lines)

    # Not found as company — search as director name
    all_companies = pb_list_all('companies')
    matches = []
    q = query.lower()
    for c in all_companies:
        directors = c.get('directors') or []
        for d in directors:
            if q in d.lower():
                matches.append((c, d))

    if not matches:
        return f"ไม่พบข้อมูลกรรมการหรือบริษัทที่ตรงกับ '{query}'"

    lines = [f"พบ '{query}' เป็นกรรมการใน {len(matches)} บริษัท:", ""]
    for c, director_name in matches:
        lines.append(f"  • {c.get('name_th', 'N/A')} — {director_name}")
    return "\n".join(lines)


@mcp.tool()
async def aggregate_stats(field: str, group_by: str = "", operation: str = "AVG") -> str:
    """คำนวณสถิติรวมของข้อมูลบริษัท เช่น ค่าเฉลี่ย ROA, ผลรวมรายได้

    Args:
        field: field ที่ต้องการคำนวณ เช่น 'roa', 'net_profit', 'total_revenue'
        group_by: จัดกลุ่มตาม field เช่น 'province', 'company_size' (optional)
        operation: SUM, AVG, MAX, MIN, COUNT (default AVG)
    """
    all_companies = pb_list_all('companies')
    if not all_companies:
        return "ไม่มีข้อมูลบริษัทในระบบ"

    op = operation.upper()

    def compute(items):
        values = [c.get(field) for c in items if c.get(field) is not None]
        if not values:
            return "N/A"
        if op == 'SUM':
            return sum(values)
        elif op == 'AVG':
            return sum(values) / len(values)
        elif op == 'MAX':
            return max(values)
        elif op == 'MIN':
            return min(values)
        elif op == 'COUNT':
            return len(values)
        return "N/A"

    if not group_by:
        result = compute(all_companies)
        if isinstance(result, float):
            return f"{op}({field}) = {result:,.2f} (จาก {len(all_companies)} บริษัท)"
        return f"{op}({field}) = {result} (จาก {len(all_companies)} บริษัท)"

    # Group by field
    groups = {}
    for c in all_companies:
        key = c.get(group_by, 'N/A') or 'N/A'
        groups.setdefault(key, []).append(c)

    lines = [f"═══ {op}({field}) grouped by {group_by} ═══", ""]
    for key in sorted(groups.keys()):
        result = compute(groups[key])
        count = len(groups[key])
        if isinstance(result, float):
            lines.append(f"  {key}: {result:,.2f} ({count} บริษัท)")
        else:
            lines.append(f"  {key}: {result} ({count} บริษัท)")

    return "\n".join(lines)


# ─── Tools: Smart Features ─────────────────────────────────────────────────

@mcp.tool()
async def get_data_summary() -> str:
    """ดูภาพรวมข้อมูลบริษัททั้งหมดในระบบ — จำนวน, ขนาด, จังหวัด, สถิติสำคัญ"""
    all_companies = pb_list_all('companies')
    if not all_companies:
        return "ไม่มีข้อมูลบริษัทในระบบ"

    total = len(all_companies)

    # By size
    by_size = {}
    for c in all_companies:
        s = c.get('company_size', 'N/A') or 'N/A'
        by_size[s] = by_size.get(s, 0) + 1

    # By province
    by_province = {}
    for c in all_companies:
        p = c.get('province', 'N/A') or 'N/A'
        by_province[p] = by_province.get(p, 0) + 1

    # Key metrics
    def avg_field(field):
        values = [c.get(field) for c in all_companies if c.get(field) is not None]
        return sum(values) / len(values) if values else None

    avg_revenue = avg_field('total_revenue')
    avg_profit = avg_field('net_profit')
    avg_roa = avg_field('roa')

    lines = [
        "═══ 📊 Data Summary ═══",
        f"จำนวนบริษัททั้งหมด: {total}",
        "",
        "─── ตามขนาด ───",
    ]
    for s, count in sorted(by_size.items()):
        lines.append(f"  {s}: {count}")

    lines.append("\n─── ตามจังหวัด ───")
    for p, count in sorted(by_province.items(), key=lambda x: -x[1]):
        lines.append(f"  {p}: {count}")

    lines.append("\n─── สถิติสำคัญ (เฉลี่ย) ───")
    if avg_revenue is not None:
        lines.append(f"  รายได้รวมเฉลี่ย: {fmt_currency(avg_revenue)}")
    if avg_profit is not None:
        lines.append(f"  กำไรสุทธิเฉลี่ย: {fmt_currency(avg_profit)}")
    if avg_roa is not None:
        lines.append(f"  ROA เฉลี่ย: {fmt_ratio(avg_roa)}")

    return "\n".join(lines)


@mcp.tool()
async def get_financial_health(company_name: str) -> str:
    """วิเคราะห์สุขภาพการเงินของบริษัท พร้อม flags และคำเตือน

    Args:
        company_name: ชื่อบริษัท
    """
    items = _lookup(company_name)
    if not items:
        return f"ไม่พบบริษัท '{company_name}'"

    c = items[0]
    lines = [
        f"═══ 🏥 Financial Health: {c.get('name_th', 'N/A')} ═══",
        f"ปีบัญชี: {c.get('fiscal_year_display', 'N/A')}",
        f"ขนาด: {c.get('company_size', 'N/A')}",
        "",
        "─── ฐานะการเงิน ───",
        f"สินทรัพย์รวม: {fmt_currency(c.get('total_assets'))}",
        f"หนี้สินรวม: {fmt_currency(c.get('total_liabilities'))}",
        f"ส่วนของผู้ถือหุ้น: {fmt_currency(c.get('equity'))}",
        "",
        "─── ผลการดำเนินงาน ───",
        f"รายได้รวม: {fmt_currency(c.get('total_revenue'))}",
        f"กำไรสุทธิ: {fmt_currency(c.get('net_profit'))}",
        f"Net Profit Margin: {fmt_ratio(c.get('net_profit_margin'))}",
        "",
        "─── สภาพคล่อง ───",
        f"Current Ratio: {fmt_ratio(c.get('current_ratio'), ' เท่า')}",
    ]

    cr = c.get('current_ratio')
    if cr is not None:
        if cr < 1:
            lines.append("  → ⚠️ ต่ำกว่า 1 — สภาพคล่องตึงตัว")
        elif cr <= 2:
            lines.append("  → ✅ ปกติ")
        else:
            lines.append("  → ✅ ดี")

    lines.append(f"\n─── Leverage ───")
    lines.append(f"D/E Ratio: {fmt_ratio(c.get('de_ratio'), ' เท่า')}")

    equity = c.get('equity')
    if equity is not None and equity < 0:
        lines.append("  → ⚠️ ส่วนของผู้ถือหุ้นติดลบ — D/E และ ROE ไม่สามารถตีความได้ตามปกติ")

    lines.append(f"\n─── ผลตอบแทน ───")
    lines.append(f"ROA: {fmt_ratio(c.get('roa'))}")
    lines.append(f"ROE: {fmt_ratio(c.get('roe'))}")

    # Flags
    flags = c.get('flags') or []
    if flags:
        lines.append(f"\n─── ⚠️ Flags ({len(flags)}) ───")
        for f in flags:
            lines.append(f"  ⚠️ {f}")

    # Overall assessment
    lines.append("\n─── สรุป ───")
    profit = c.get('net_profit')
    if profit is not None:
        if profit > 0:
            lines.append(f"  ✅ มีกำไรสุทธิ {fmt_currency(profit)}")
        else:
            lines.append(f"  ❌ ขาดทุนสุทธิ {fmt_currency(abs(profit))}")

    return "\n".join(lines)


@mcp.tool()
async def search_companies(query: str) -> str:
    """ค้นหาบริษัทจากข้อความ ค้นจากชื่อ ประเภทธุรกิจ จังหวัด วัตถุประสงค์

    Args:
        query: ข้อความที่ต้องการค้นหา
    """
    all_companies = pb_list_all('companies')
    q = query.lower()
    matches = []
    for c in all_companies:
        searchable = ' '.join([
            c.get('name_th', ''),
            c.get('name_en', ''),
            c.get('business_type', ''),
            c.get('business_category', ''),
            c.get('objective', ''),
            c.get('province', ''),
        ]).lower()
        if q in searchable:
            matches.append(c)

    if not matches:
        return f"ไม่พบบริษัทที่ตรงกับ '{query}'"

    lines = [f"พบ {len(matches)} บริษัทที่ตรงกับ '{query}':", ""]
    for i, c in enumerate(matches, 1):
        lines.append(fmt_company_card(c, i))
    return "\n".join(lines)


@mcp.tool()
async def get_company_profile(company_name: str) -> str:
    """แสดงข้อมูลครบทุก field ของบริษัท — ข้อมูลทั่วไป, การเงิน, อัตราส่วน, กรรมการ

    Args:
        company_name: ชื่อบริษัท
    """
    items = _lookup(company_name)
    if not items:
        return f"ไม่พบบริษัท '{company_name}'"

    return fmt_company_detail(items[0])


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport == "sse":
        print(f"🔌 Creden AI MCP SSE on http://{MCP_HOST}:{MCP_PORT}/sse", file=sys.stderr)
        mcp.run(transport="sse")
    else:
        print("🔌 Creden AI MCP Server (stdio)", file=sys.stderr)
        mcp.run(transport="stdio")
