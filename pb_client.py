"""
PocketBase REST API client for Company Data App.
Reusable CRUD helpers — same pattern as aiya-todo.
"""
import os, json, requests

PB_URL = os.environ.get('POCKETBASE_URL', 'http://127.0.0.1:8090')
PB_EMAIL = os.environ.get('PB_EMAIL', 'admin@company.local')
PB_PASSWORD = os.environ.get('PB_PASSWORD', 'adminpassword1234')

_token = None
_session = requests.Session()

def _get_token():
    """Get or refresh superuser auth token."""
    global _token
    if _token:
        return _token
    resp = _session.post(f'{PB_URL}/api/collections/_superusers/auth-with-password',
                         json={'identity': PB_EMAIL, 'password': PB_PASSWORD}, timeout=30)
    resp.raise_for_status()
    _token = resp.json()['token']
    _session.headers['Authorization'] = f'Bearer {_token}'
    return _token

def _ensure_auth():
    if 'Authorization' not in _session.headers:
        _get_token()

# ─── Public API ──────────────────────────────────────────────────────────────

def pb_list(collection, filter='', sort='', limit=200, page=1):
    """List records from a collection."""
    _ensure_auth()
    params = {'perPage': limit, 'page': page}
    if filter:
        params['filter'] = filter
    if sort:
        params['sort'] = sort
    resp = _session.get(f'{PB_URL}/api/collections/{collection}/records', params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get('items', [])

def pb_list_all(collection, filter='', sort=''):
    """List ALL records (handles pagination)."""
    all_items = []
    page = 1
    while True:
        items = pb_list(collection, filter=filter, sort=sort, limit=200, page=page)
        all_items.extend(items)
        if len(items) < 200:
            break
        page += 1
    return all_items

def pb_get(collection, record_id):
    """Get a single record by ID."""
    _ensure_auth()
    resp = _session.get(f'{PB_URL}/api/collections/{collection}/records/{record_id}', timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()

def pb_create(collection, data):
    """Create a new record."""
    _ensure_auth()
    resp = _session.post(f'{PB_URL}/api/collections/{collection}/records', json=data, timeout=30)
    resp.raise_for_status()
    return resp.json()

def pb_update(collection, record_id, data):
    """Update an existing record."""
    _ensure_auth()
    resp = _session.patch(f'{PB_URL}/api/collections/{collection}/records/{record_id}', json=data, timeout=30)
    resp.raise_for_status()
    return resp.json()

def pb_delete(collection, record_id):
    """Delete a record."""
    _ensure_auth()
    resp = _session.delete(f'{PB_URL}/api/collections/{collection}/records/{record_id}', timeout=30)
    if resp.status_code == 404:
        return False
    resp.raise_for_status()
    return True

def pb_first(collection, filter=''):
    """Get first matching record or None."""
    items = pb_list(collection, filter=filter, limit=1)
    return items[0] if items else None

def pb_count(collection, filter=''):
    """Count records matching filter."""
    _ensure_auth()
    params = {'perPage': 1}
    if filter:
        params['filter'] = filter
    resp = _session.get(f'{PB_URL}/api/collections/{collection}/records', params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get('totalItems', 0)
