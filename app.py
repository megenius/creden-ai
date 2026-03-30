"""
Creden AI — Flask REST API + Web UI
แอปดูข้อมูลบริษัทไทย สำหรับ Workshop: เรียนรู้การพัฒนาซอฟต์แวร์ด้วย AI Agent

Architecture mirrors aiya-todo: Flask REST API + PocketBase DB + MCP Server
"""

import os, json
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory

from pb_client import pb_list, pb_list_all, pb_get, PB_URL

app = Flask(__name__, static_folder='ui')

def now():
    return datetime.utcnow().isoformat()

# ─── CORS ────────────────────────────────────────────────────────────────────

ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', '*').split(',')

@app.after_request
def cors(r):
    origin = request.headers.get('Origin', '')
    if origin in ALLOWED_ORIGINS or '*' in ALLOWED_ORIGINS:
        r.headers['Access-Control-Allow-Origin'] = origin or '*'
        r.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        r.headers['Access-Control-Allow-Methods'] = 'GET,OPTIONS'
        r.headers['Vary'] = 'Origin'
    return r

@app.before_request
def handle_options():
    if request.method == 'OPTIONS':
        return '', 204

# ─── Health Check ────────────────────────────────────────────────────────────

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'app': 'creden-ai', 'time': now()})

# ─── Static UI ───────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('ui', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('ui', path)

# ─── API: Companies ─────────────────────────────────────────────────────────

@app.route('/api/companies', methods=['GET'])
def list_companies():
    """List companies with optional filters."""
    province = request.args.get('province')
    company_size = request.args.get('company_size')
    sort = request.args.get('sort', '-total_revenue')
    try:
        limit = int(request.args.get('limit', 50))
    except ValueError:
        limit = 50

    parts = []
    if province:
        parts.append(f'province = "{province}"')
    if company_size:
        parts.append(f'company_size = "{company_size}"')

    filter_str = ' && '.join(parts) if parts else ''
    items = pb_list('companies', filter=filter_str, sort=sort, limit=limit)

    return jsonify({'items': items, 'total': len(items)})


@app.route('/api/companies/search', methods=['GET'])
def search_companies():
    """Search companies by name, juristic_id, or business type."""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'items': [], 'total': 0})

    # Try juristic_id first
    if q.replace(" ", "").isdigit() and len(q.replace(" ", "")) == 13:
        filter_str = f'juristic_id = "{q.replace(" ", "")}"'
    else:
        filter_str = f'name_th ~ "{q}" || name_en ~ "{q}" || business_type ~ "{q}"'

    try:
        items = pb_list('companies', filter=filter_str, limit=20)
    except Exception:
        return jsonify({'items': [], 'total': 0})

    return jsonify({'items': items, 'total': len(items)})


@app.route('/api/companies/<company_id>', methods=['GET'])
def get_company(company_id):
    """Get a single company by PocketBase ID."""
    company = pb_get('companies', company_id)
    if not company:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(company)


# ─── API: Stats ──────────────────────────────────────────────────────────────

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Get aggregate statistics."""
    all_companies = pb_list_all('companies')

    total = len(all_companies)
    if total == 0:
        return jsonify({'total': 0})

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

    # Aggregates
    def safe_avg(field):
        values = [c.get(field) for c in all_companies if c.get(field) is not None]
        return round(sum(values) / len(values), 2) if values else None

    def safe_sum(field):
        values = [c.get(field) for c in all_companies if c.get(field) is not None]
        return round(sum(values), 2) if values else None

    return jsonify({
        'total': total,
        'by_size': by_size,
        'by_province': by_province,
        'total_revenue_sum': safe_sum('total_revenue'),
        'avg_net_profit': safe_avg('net_profit'),
        'avg_roa': safe_avg('roa'),
    })


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8766))
    print(f"🚀 Creden AI running on http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=True)
