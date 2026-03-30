"""
Gemini Embedding API client — lightweight, no SDK.
Used only for generating text embeddings for sqlite_vec.
"""
import os, hashlib, math, struct, sys, requests

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
EMBED_MODEL = 'gemini-embedding-001'
EMBED_DIM = 768
EMBED_URL = f'https://generativelanguage.googleapis.com/v1beta/models/{EMBED_MODEL}:embedContent'


def get_embedding(text: str) -> list[float]:
    """Get 768-dim embedding for text. Falls back to hash-based pseudo-embedding if no API key."""
    if not GEMINI_API_KEY:
        return _fallback_embedding(text)

    try:
        resp = requests.post(
            f'{EMBED_URL}?key={GEMINI_API_KEY}',
            json={'content': {'parts': [{'text': text}]}},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()['embedding']['values']
    except Exception as e:
        print(f"   ⚠️  Gemini embedding failed: {e} — using fallback", file=sys.stderr)
        return _fallback_embedding(text)


def _fallback_embedding(text: str) -> list[float]:
    """Deterministic hash-based pseudo-embedding (768-dim). Quality is poor but app won't crash."""
    h = hashlib.sha256(text.encode('utf-8')).digest()
    values = []
    for i in range(EMBED_DIM):
        seed = hashlib.md5(h + struct.pack('H', i)).digest()
        val = struct.unpack('f', seed[:4])[0]
        if not math.isfinite(val):
            val = 0.0
        # Normalize to [-1, 1]
        val = (val % 2.0) - 1.0
        values.append(val)
    return values
