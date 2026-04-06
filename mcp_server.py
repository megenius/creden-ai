"""
Creden AI — MCP Server
Exposes company data tools for AI assistants via Model Context Protocol.
Claude Code is the agent — these tools let it query Thai company data.

Architecture mirrors aiya-todo: FastMCP + pb_client pattern
"""
import os, sys, json, secrets, time
from dataclasses import dataclass, field
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.provider import (
    OAuthAuthorizationServerProvider, AuthorizationParams,
    AccessToken, AuthorizationCode, RefreshToken
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pb_client import pb_list, pb_list_all, pb_get

# ─── Config ──────────────────────────────────────────────────────────────────

MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MCP_PORT", os.environ.get("PORT", "8767")))
MCP_OAUTH = os.environ.get("MCP_OAUTH", "0") == "1"

# ─── OAuth Provider ──────────────────────────────────────────────────────────

@dataclass
class SimpleAuthCode:
    code: str
    client_id: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool = True
    scopes: list = field(default_factory=list)
    expires_at: float = 0
    code_challenge: str = ''
    user_email: str = ''

@dataclass
class SimpleAccessToken:
    token: str
    client_id: str
    scopes: list = field(default_factory=list)
    expires_at: float = 0
    user_email: str = ''

@dataclass
class SimpleRefreshToken:
    token: str
    client_id: str
    scopes: list = field(default_factory=list)
    user_email: str = ''

class CredenOAuthProvider(OAuthAuthorizationServerProvider[SimpleAuthCode, SimpleAccessToken, SimpleRefreshToken]):
    """OAuth provider with PocketBase login for MCP SSE protection."""

    def __init__(self):
        self._clients = {}
        self._auth_codes = {}
        self._access_tokens = {}
        self._refresh_tokens = {}
        self._pending_auth = {}

    async def get_client(self, client_id: str):
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull):
        if not client_info.scope:
            client_info.scope = 'claudeai'
        elif 'claudeai' not in client_info.scope:
            client_info.scope = client_info.scope + ' claudeai'
        self._clients[client_info.client_id] = client_info

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        pending_id = secrets.token_urlsafe(16)
        self._pending_auth[pending_id] = {
            'client_id': client.client_id,
            'redirect_uri': str(params.redirect_uri),
            'redirect_uri_provided_explicitly': params.redirect_uri_provided_explicitly,
            'scopes': params.scopes or [],
            'code_challenge': params.code_challenge or '',
            'state': params.state or '',
            'expires_at': time.time() + 600,
        }
        issuer = os.environ.get("MCP_ISSUER", f"http://{MCP_HOST}:{MCP_PORT}")
        return f"{issuer}/login?pending={pending_id}"

    def complete_auth(self, pending_id, user_email=''):
        """Called after successful login. Returns redirect URL with auth code."""
        pending = self._pending_auth.pop(pending_id, None)
        if not pending or pending['expires_at'] < time.time():
            return None
        code = secrets.token_urlsafe(32)
        self._auth_codes[code] = SimpleAuthCode(
            code=code,
            client_id=pending['client_id'],
            redirect_uri=pending['redirect_uri'],
            redirect_uri_provided_explicitly=pending['redirect_uri_provided_explicitly'],
            scopes=pending['scopes'],
            expires_at=time.time() + 300,
            code_challenge=pending['code_challenge'],
            user_email=user_email,
        )
        sep = '&' if '?' in pending['redirect_uri'] else '?'
        redirect = f"{pending['redirect_uri']}{sep}code={code}"
        if pending['state']:
            redirect += f"&state={pending['state']}"
        return redirect

    async def load_authorization_code(self, client, authorization_code: str):
        ac = self._auth_codes.get(authorization_code)
        if ac and ac.client_id == client.client_id and ac.expires_at > time.time():
            return ac
        return None

    async def exchange_authorization_code(self, client, authorization_code):
        self._auth_codes.pop(authorization_code.code, None)
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        self._access_tokens[access] = SimpleAccessToken(
            token=access, client_id=client.client_id,
            scopes=authorization_code.scopes, expires_at=time.time() + 86400,
            user_email=authorization_code.user_email,
        )
        self._refresh_tokens[refresh] = SimpleRefreshToken(
            token=refresh, client_id=client.client_id,
            scopes=authorization_code.scopes,
            user_email=authorization_code.user_email,
        )
        return OAuthToken(access_token=access, token_type="bearer", expires_in=86400, refresh_token=refresh)

    async def load_access_token(self, token: str):
        at = self._access_tokens.get(token)
        if at and at.expires_at > time.time():
            return at
        return None

    async def load_refresh_token(self, client, refresh_token: str):
        rt = self._refresh_tokens.get(refresh_token)
        if rt and rt.client_id == client.client_id:
            return rt
        return None

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        access = secrets.token_urlsafe(32)
        self._access_tokens[access] = SimpleAccessToken(
            token=access, client_id=client.client_id,
            scopes=scopes or refresh_token.scopes, expires_at=time.time() + 86400,
            user_email=refresh_token.user_email,
        )
        return OAuthToken(access_token=access, token_type="bearer", expires_in=86400,
                          refresh_token=refresh_token.token)

    async def revoke_token(self, token):
        if hasattr(token, 'token'):
            self._access_tokens.pop(token.token, None)
            self._refresh_tokens.pop(token.token, None)

# ─── Create MCP Server ──────────────────────────────────────────────────────

MCP_INSTRUCTIONS = """Creden AI — ข้อมูลบริษัทไทยจาก DBD + งบการเงิน

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

if MCP_OAUTH:
    from mcp.server.fastmcp.server import AuthSettings
    from mcp.server.auth.settings import ClientRegistrationOptions
    MCP_ISSUER = os.environ.get("MCP_ISSUER", f"https://mcp-creden-production.up.railway.app")
    mcp = FastMCP(
        "creden-ai",
        host=MCP_HOST,
        port=MCP_PORT,
        auth_server_provider=CredenOAuthProvider(),
        auth=AuthSettings(
            issuer_url=MCP_ISSUER,
            resource_server_url=MCP_ISSUER,
            client_registration_options=ClientRegistrationOptions(enabled=True),
        ),
        instructions=MCP_INSTRUCTIONS,
    )
else:
    mcp = FastMCP(
        "creden-ai",
        host=MCP_HOST,
        port=MCP_PORT,
        instructions=MCP_INSTRUCTIONS,
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


# ─── Login Page HTML ────────────────────────────────────────────────────────

LOGIN_HTML = '''<!DOCTYPE html><html lang="th"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Creden AI - Login</title>
<link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Sarabun',sans-serif;background:linear-gradient(135deg,#0f172a 0%,#1e293b 50%,#334155 100%);
min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:white;border-radius:16px;padding:40px;width:100%;max-width:400px;box-shadow:0 20px 60px rgba(0,0,0,0.3)}
h1{font-size:28px;font-weight:800;background:linear-gradient(135deg,#2563eb,#7c3aed);
-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:4px}
.sub{color:#6b7280;font-size:14px;margin-bottom:32px}
.field{margin-bottom:20px}
label{display:block;font-size:14px;font-weight:600;margin-bottom:6px;color:#374151}
input{width:100%;padding:12px 16px;border:2px solid #e5e7eb;border-radius:8px;font-size:14px;
font-family:'Sarabun',sans-serif;transition:border-color 0.2s}
input:focus{outline:none;border-color:#2563eb}
button{width:100%;padding:14px;background:#2563eb;color:white;border:none;border-radius:8px;
font-size:16px;font-weight:700;cursor:pointer;font-family:'Sarabun',sans-serif;transition:background 0.2s}
button:hover{background:#1d4ed8}
.error{background:#fef2f2;color:#dc2626;padding:12px;border-radius:8px;font-size:13px;margin-bottom:16px;display:none}
.logo{font-size:36px;margin-bottom:8px}
</style></head><body>
<div class="card">
<div class="logo">🏢</div>
<h1>Creden AI</h1>
<div class="sub">เข้าสู่ระบบเพื่อเชื่อมต่อกับ Claude AI</div>
<div class="error" id="error"></div>
<form method="POST" action="/login">
<input type="hidden" name="pending" value="{pending}">
<div class="field"><label>Email</label><input type="email" name="email" required autofocus></div>
<div class="field"><label>Password</label><input type="password" name="password" required></div>
<button type="submit">เข้าสู่ระบบ</button>
</form>
</div></body></html>'''

LOGIN_ERROR_HTML = LOGIN_HTML.replace('display:none', 'display:block').replace(
    '<div class="error" id="error"></div>',
    '<div class="error" id="error">{error}</div>')

SUCCESS_HTML = '''<!DOCTYPE html><html lang="th"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Creden AI - Connected</title>
<link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Sarabun',sans-serif;background:linear-gradient(135deg,#0f172a 0%,#1e293b 50%,#334155 100%);
min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:white;border-radius:16px;padding:40px;width:100%;max-width:400px;box-shadow:0 20px 60px rgba(0,0,0,0.3);text-align:center}
.icon{font-size:64px;margin-bottom:16px}
h1{font-size:24px;font-weight:800;color:#10b981;margin-bottom:8px}
.sub{color:#6b7280;font-size:14px;margin-bottom:24px;line-height:1.6}
.user{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:12px;font-size:14px;color:#065f46;font-weight:600;margin-bottom:24px}
.close-hint{color:#9ca3af;font-size:13px}
</style></head><body>
<div class="card">
<div class="icon">✅</div>
<h1>เชื่อมต่อสำเร็จ!</h1>
<div class="sub">Creden AI เชื่อมต่อกับ Claude AI เรียบร้อยแล้ว<br>สามารถค้นหาข้อมูลบริษัทไทยได้ทันที</div>
<div class="user">👤 {email}</div>
<div class="close-hint">คุณสามารถปิดหน้านี้ได้เลย</div>
</div>
<script>setTimeout(function(){window.close()},5000)</script>
</body></html>'''


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport == "sse":
        print(f"🔌 Creden AI MCP SSE on http://{MCP_HOST}:{MCP_PORT}/sse", file=sys.stderr)

        if MCP_OAUTH:
            import uvicorn
            from starlette.applications import Starlette
            from starlette.requests import Request
            from starlette.responses import HTMLResponse
            from starlette.routing import Route, Mount

            oauth_provider = mcp._auth_server_provider

            async def login_get(request: Request):
                pending = request.query_params.get('pending', '')
                if not pending or pending not in oauth_provider._pending_auth:
                    return HTMLResponse('<h1>Invalid or expired link</h1>', status_code=400)
                return HTMLResponse(LOGIN_HTML.replace('{pending}', pending))

            async def login_post(request: Request):
                form = await request.form()
                pending = form.get('pending', '')
                email = form.get('email', '')
                password = form.get('password', '')

                if not pending or pending not in oauth_provider._pending_auth:
                    return HTMLResponse('<h1>Session expired. Please try again.</h1>', status_code=400)

                # Verify against PocketBase users collection
                import requests as req
                pb_url = os.environ.get('POCKETBASE_URL', 'http://127.0.0.1:8090')
                try:
                    resp = req.post(f'{pb_url}/api/collections/users/auth-with-password',
                                    json={'identity': email, 'password': password}, timeout=10)
                    if resp.status_code != 200:
                        html = LOGIN_ERROR_HTML.replace('{pending}', pending).replace('{error}', 'อีเมลหรือรหัสผ่านไม่ถูกต้อง')
                        return HTMLResponse(html)
                except Exception:
                    html = LOGIN_ERROR_HTML.replace('{pending}', pending).replace('{error}', 'ไม่สามารถเชื่อมต่อระบบได้')
                    return HTMLResponse(html)

                # Login success -> complete OAuth flow
                redirect_url = oauth_provider.complete_auth(pending, user_email=email)
                if not redirect_url:
                    return HTMLResponse('<h1>Session expired. Please try again.</h1>', status_code=400)
                success_html = SUCCESS_HTML.replace('{email}', email)
                success_html = success_html.replace('</head>',
                    f'<meta http-equiv="refresh" content="2;url={redirect_url}"></head>')
                return HTMLResponse(success_html)

            # Mount login routes + MCP SSE app
            mcp_app = mcp.sse_app()
            app = Starlette(routes=[
                Route('/login', login_get, methods=['GET']),
                Route('/login', login_post, methods=['POST']),
                Mount('/', app=mcp_app),
            ])

            uvicorn.run(app, host=MCP_HOST, port=MCP_PORT)
        else:
            mcp.run(transport="sse")
    else:
        print("🔌 Creden AI MCP Server (stdio)", file=sys.stderr)
        mcp.run(transport="stdio")
