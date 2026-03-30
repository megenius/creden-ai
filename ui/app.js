/**
 * Creden AI — Frontend
 * Vanilla JS, no frameworks
 */

const API = '/api';

// ─── API Helper ─────────────────────────────────────────────────────────────

async function api(path) {
    const resp = await fetch(`${API}${path}`);
    if (!resp.ok) throw new Error(`API error: ${resp.status}`);
    return resp.json();
}

// ─── Formatting ─────────────────────────────────────────────────────────────

function fmtCurrency(val) {
    if (val == null) return 'N/A';
    if (Math.abs(val) >= 1000) return val.toLocaleString('th-TH', { maximumFractionDigits: 0 }) + ' ล้าน฿';
    return val.toLocaleString('th-TH', { maximumFractionDigits: 2 }) + ' ล้าน฿';
}

function fmtPercent(val) {
    if (val == null) return 'N/A';
    return val.toFixed(2) + '%';
}

function fmtRatio(val) {
    if (val == null) return 'N/A';
    return val.toFixed(2) + ' เท่า';
}

function sizeIcon(size) {
    return size === 'บริษัทขนาดใหญ่' ? '🏢' : '🏠';
}

function profitClass(val) {
    if (val == null) return '';
    return val >= 0 ? 'positive' : 'negative';
}

// ─── Load Data ──────────────────────────────────────────────────────────────

let currentFilter = '';

async function loadStats() {
    try {
        const data = await api('/stats');
        document.getElementById('statTotal').textContent = data.total || 0;
        document.getElementById('statRevenue').textContent = data.total_revenue_sum != null
            ? data.total_revenue_sum.toLocaleString('th-TH', { maximumFractionDigits: 0 })
            : '-';
        document.getElementById('statROA').textContent = data.avg_roa != null
            ? data.avg_roa.toFixed(2)
            : '-';
    } catch (e) {
        console.error('Failed to load stats:', e);
    }
}

async function loadCompanies(filterSize) {
    const list = document.getElementById('companyList');
    list.innerHTML = '<p class="loading">กำลังโหลดข้อมูล...</p>';

    try {
        let url = '/companies?sort=-total_revenue&limit=50';
        if (filterSize) url += `&company_size=${encodeURIComponent(filterSize)}`;

        const data = await api(url);
        renderCompanies(data.items);
    } catch (e) {
        list.innerHTML = `<p class="error">❌ ไม่สามารถโหลดข้อมูลได้: ${e.message}</p>`;
    }
}

async function doSearch() {
    const q = document.getElementById('searchInput').value.trim();
    if (!q) {
        loadCompanies(currentFilter);
        return;
    }

    const list = document.getElementById('companyList');
    list.innerHTML = '<p class="loading">กำลังค้นหา...</p>';

    try {
        const data = await api(`/companies/search?q=${encodeURIComponent(q)}`);
        if (data.items.length === 0) {
            list.innerHTML = `<p class="empty">ไม่พบบริษัทที่ตรงกับ "${q}"</p>`;
        } else {
            renderCompanies(data.items);
        }
    } catch (e) {
        list.innerHTML = `<p class="error">❌ ค้นหาล้มเหลว: ${e.message}</p>`;
    }
}

function setFilter(size, btn) {
    currentFilter = size;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('searchInput').value = '';
    loadCompanies(size);
}

// ─── Render ─────────────────────────────────────────────────────────────────

function renderCompanies(companies) {
    const list = document.getElementById('companyList');

    if (!companies || companies.length === 0) {
        list.innerHTML = '<p class="empty">ไม่พบข้อมูลบริษัท</p>';
        return;
    }

    list.innerHTML = companies.map(c => `
        <div class="company-card" onclick="showDetail('${c.id}')">
            <div class="card-header">
                <span class="size-icon">${sizeIcon(c.company_size)}</span>
                <div class="card-title">
                    <h3>${c.name_th || 'N/A'}</h3>
                    <span class="name-en">${c.name_en || ''}</span>
                </div>
                <span class="badge ${c.company_size === 'บริษัทขนาดใหญ่' ? 'badge-large' : 'badge-sme'}">
                    ${c.company_size === 'บริษัทขนาดใหญ่' ? 'ใหญ่' : 'SME'}
                </span>
            </div>
            <div class="card-meta">
                <span>📍 ${c.province || 'N/A'}</span>
                <span>💼 ${c.business_type || 'N/A'}</span>
            </div>
            <div class="card-metrics">
                <div class="metric">
                    <span class="metric-label">รายได้</span>
                    <span class="metric-value">${fmtCurrency(c.total_revenue)}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">กำไรสุทธิ</span>
                    <span class="metric-value ${profitClass(c.net_profit)}">${fmtCurrency(c.net_profit)}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">ROA</span>
                    <span class="metric-value">${fmtPercent(c.roa)}</span>
                </div>
                <div class="metric">
                    <span class="metric-label">D/E</span>
                    <span class="metric-value">${fmtRatio(c.de_ratio)}</span>
                </div>
            </div>
        </div>
    `).join('');
}

// ─── Detail Modal ───────────────────────────────────────────────────────────

async function showDetail(id) {
    const modal = document.getElementById('modalOverlay');
    const content = document.getElementById('modalContent');
    content.innerHTML = '<p class="loading">กำลังโหลด...</p>';
    modal.classList.add('active');

    try {
        const c = await api(`/companies/${id}`);
        const flags = c.flags || [];
        const directors = c.directors || [];

        content.innerHTML = `
            <h2>${sizeIcon(c.company_size)} ${c.name_th || 'N/A'}</h2>
            <p class="detail-subtitle">${c.name_en || ''} | ${c.juristic_id || ''}</p>

            <div class="detail-section">
                <h4>ข้อมูลทั่วไป</h4>
                <table class="detail-table">
                    <tr><td>ประเภท</td><td>${c.entity_type || 'N/A'}</td></tr>
                    <tr><td>สถานะ</td><td>${c.status || 'N/A'}</td></tr>
                    <tr><td>ธุรกิจ</td><td>${c.business_type || 'N/A'}</td></tr>
                    <tr><td>หมวด</td><td>${c.business_category || 'N/A'}</td></tr>
                    <tr><td>วัตถุประสงค์</td><td>${c.objective || 'N/A'}</td></tr>
                    <tr><td>จังหวัด</td><td>${c.province || 'N/A'} (${c.region || ''})</td></tr>
                    <tr><td>ที่อยู่</td><td>${c.address || 'N/A'}</td></tr>
                    <tr><td>โทรศัพท์</td><td>${c.phone || 'N/A'}</td></tr>
                    <tr><td>ขนาด</td><td>${c.company_size || 'N/A'}</td></tr>
                    <tr><td>จดทะเบียน</td><td>${c.reg_date_display || 'N/A'}</td></tr>
                    <tr><td>ปีบัญชี</td><td>${c.fiscal_year_display || 'N/A'}</td></tr>
                </table>
            </div>

            <div class="detail-section">
                <h4>งบการเงิน (ล้านบาท)</h4>
                <table class="detail-table">
                    <tr><td>ทุนจดทะเบียน</td><td>${fmtCurrency(c.registered_capital)}</td></tr>
                    <tr><td>สินทรัพย์รวม</td><td>${fmtCurrency(c.total_assets)}</td></tr>
                    <tr><td>หนี้สินรวม</td><td>${fmtCurrency(c.total_liabilities)}</td></tr>
                    <tr><td>ส่วนของผู้ถือหุ้น</td><td class="${profitClass(c.equity)}">${fmtCurrency(c.equity)}</td></tr>
                    <tr><td>รายได้รวม</td><td>${fmtCurrency(c.total_revenue)}</td></tr>
                    <tr><td>รายได้หลัก</td><td>${fmtCurrency(c.main_revenue)}</td></tr>
                    <tr><td>กำไรขั้นต้น</td><td>${fmtCurrency(c.gross_profit)}</td></tr>
                    <tr><td>ต้นทุนขาย</td><td>${fmtCurrency(c.cost_of_sales)}</td></tr>
                    <tr><td>รายจ่ายรวม</td><td>${fmtCurrency(c.total_expenses)}</td></tr>
                    <tr><td>กำไรสุทธิ</td><td class="${profitClass(c.net_profit)}">${fmtCurrency(c.net_profit)}</td></tr>
                </table>
            </div>

            <div class="detail-section">
                <h4>อัตราส่วนทางการเงิน</h4>
                <table class="detail-table">
                    <tr><td>Current Ratio</td><td>${fmtRatio(c.current_ratio)}</td></tr>
                    <tr><td>D/E Ratio</td><td>${fmtRatio(c.de_ratio)}</td></tr>
                    <tr><td>ROA</td><td>${fmtPercent(c.roa)}</td></tr>
                    <tr><td>ROE</td><td>${fmtPercent(c.roe)}</td></tr>
                    <tr><td>Net Profit Margin</td><td>${fmtPercent(c.net_profit_margin)}</td></tr>
                    <tr><td>Gross Profit Margin</td><td>${fmtPercent(c.gross_profit_margin)}</td></tr>
                    <tr><td>Asset Turnover</td><td>${fmtRatio(c.asset_turnover)}</td></tr>
                </table>
            </div>

            ${directors.length > 0 ? `
            <div class="detail-section">
                <h4>กรรมการ (${directors.length} คน)</h4>
                <ul class="directors-list">
                    ${directors.map(d => `<li>${d}</li>`).join('')}
                </ul>
            </div>
            ` : ''}

            ${flags.length > 0 ? `
            <div class="detail-section flags-section">
                <h4>⚠️ Flags</h4>
                <ul class="flags-list">
                    ${flags.map(f => `<li>⚠️ ${f}</li>`).join('')}
                </ul>
            </div>
            ` : ''}
        `;
    } catch (e) {
        content.innerHTML = `<p class="error">❌ ไม่สามารถโหลดข้อมูลได้: ${e.message}</p>`;
    }
}

function closeModal() {
    document.getElementById('modalOverlay').classList.remove('active');
}

// Close modal on Escape
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeModal();
});

// ─── Init ───────────────────────────────────────────────────────────────────

loadStats();
loadCompanies();
