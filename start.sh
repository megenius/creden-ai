#!/bin/bash
# Creden AI — One-Command Startup
# รันครั้งแรก: chmod +x start.sh && ./start.sh
# ทุกอย่าง auto: download PocketBase + sqlite_vec + setup DB + import data + start servers

set -e
cd "$(dirname "$0")"

PB_VERSION="0.25.9"
PB_PORT=8090
WEB_PORT=8766
SQLITE_VEC_VERSION="0.1.6"

echo "🏢 Creden AI — ข้อมูลบริษัทไทย"
echo "   Web UI     : http://localhost:${WEB_PORT}"
echo "   PocketBase : http://localhost:${PB_PORT}/_/"
echo "   MCP        : claude mcp add creden-ai -- python mcp_server.py"
echo "   หยุด       : Ctrl+C"
echo ""

# ── 1. Find Python 3 ────────────────────────────────────────────────────────
if command -v python3 &>/dev/null; then
    PY=python3
elif command -v python &>/dev/null && python --version 2>&1 | grep -q "Python 3"; then
    PY=python
else
    echo "❌ ไม่พบ Python 3 — ติดตั้งได้ที่ https://python.org"
    exit 1
fi
echo "   Python: $($PY --version)"

# ── 2. Download PocketBase (ถ้ายังไม่มี) ────────────────────────────────────
PB_DIR="$(pwd)/pb_data"
PB_BIN="$(pwd)/pocketbase_bin"
mkdir -p "$PB_DIR"

if [ ! -f "$PB_BIN/pocketbase" ]; then
    echo ""
    echo "📦 Downloading PocketBase v${PB_VERSION}..."

    OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)  ARCH="amd64" ;;
        aarch64|arm64) ARCH="arm64" ;;
        *)
            echo "❌ Unsupported architecture: $ARCH"
            exit 1
            ;;
    esac

    case "$OS" in
        darwin) OS="darwin" ;;
        linux)  OS="linux" ;;
        *)
            echo "❌ Unsupported OS: $OS"
            exit 1
            ;;
    esac

    PB_URL="https://github.com/pocketbase/pocketbase/releases/download/v${PB_VERSION}/pocketbase_${PB_VERSION}_${OS}_${ARCH}.zip"
    mkdir -p "$PB_BIN"
    curl -fsSL -o /tmp/pocketbase.zip "$PB_URL"
    unzip -qo /tmp/pocketbase.zip -d "$PB_BIN"
    rm /tmp/pocketbase.zip
    chmod +x "$PB_BIN/pocketbase"
    echo "   ✅ PocketBase downloaded to $PB_BIN/"
else
    echo "   ✅ PocketBase found"
fi

# ── 2b. Download sqlite_vec (ถ้ายังไม่มี) ──────────────────────────────────
VEC_BIN="$(pwd)/sqlite_vec_bin"

if [ ! -d "$VEC_BIN" ] || [ -z "$(ls -A "$VEC_BIN" 2>/dev/null)" ]; then
    echo ""
    echo "📦 Downloading sqlite_vec v${SQLITE_VEC_VERSION}..."

    OS_NAME=$(uname -s | tr '[:upper:]' '[:lower:]')
    ARCH_NAME=$(uname -m)

    case "$OS_NAME" in
        darwin)
            case "$ARCH_NAME" in
                arm64|aarch64) VEC_FILE="sqlite-vec-${SQLITE_VEC_VERSION}-loadable-macos-aarch64.tar.gz" ;;
                x86_64) VEC_FILE="sqlite-vec-${SQLITE_VEC_VERSION}-loadable-macos-x86_64.tar.gz" ;;
            esac
            ;;
        linux)
            case "$ARCH_NAME" in
                x86_64) VEC_FILE="sqlite-vec-${SQLITE_VEC_VERSION}-loadable-linux-x86_64.tar.gz" ;;
                aarch64|arm64) VEC_FILE="sqlite-vec-${SQLITE_VEC_VERSION}-loadable-linux-aarch64.tar.gz" ;;
            esac
            ;;
    esac

    if [ -n "$VEC_FILE" ]; then
        VEC_URL="https://github.com/asg017/sqlite-vec/releases/download/v${SQLITE_VEC_VERSION}/${VEC_FILE}"
        mkdir -p "$VEC_BIN"
        curl -fsSL -o /tmp/sqlite_vec.tar.gz "$VEC_URL" 2>/dev/null && \
        tar -xzf /tmp/sqlite_vec.tar.gz -C "$VEC_BIN" 2>/dev/null && \
        rm /tmp/sqlite_vec.tar.gz && \
        echo "   ✅ sqlite_vec downloaded to $VEC_BIN/" || \
        echo "   ⚠️  sqlite_vec download failed — vector search will be disabled (app still works)"
    else
        echo "   ⚠️  sqlite_vec not available for this platform — vector search disabled"
    fi
else
    echo "   ✅ sqlite_vec found"
fi

# ── 3. Create Virtual Environment ───────────────────────────────────────────
VENV="$(pwd)/venv"
if [ ! -d "$VENV" ]; then
    echo ""
    echo "📦 สร้าง virtual environment..."
    $PY -m venv "$VENV"
    echo "   ✅ venv created"
fi

source "$VENV/bin/activate"
echo "   ✅ venv active: $(python --version)"

# ── 4. Install packages ────────────────────────────────────────────────────
if ! python -c "import flask, mcp" 2>/dev/null; then
    echo ""
    echo "📦 ติดตั้ง packages..."
    pip install --quiet -r requirements.txt
    echo "   ✅ packages installed"
fi

# ── 5. Start PocketBase (background) ───────────────────────────────────────
echo ""
echo "🗄️  Starting PocketBase..."
"$PB_BIN/pocketbase" serve --http="127.0.0.1:${PB_PORT}" --dir="$PB_DIR" &
PB_PID=$!

# Wait for PB to be ready
for i in $(seq 1 15); do
    if curl -sf "http://127.0.0.1:${PB_PORT}/api/health" >/dev/null 2>&1; then
        echo "   ✅ PocketBase running on port ${PB_PORT}"
        break
    fi
    sleep 1
done

# ── 6. Create superuser via CLI (first run) ─────────────────────────────────
# PocketBase 0.25+ requires CLI-based superuser creation on first run
PB_ADMIN_EMAIL="${PB_EMAIL:-admin@company.local}"
PB_ADMIN_PASS="${PB_PASSWORD:-adminpassword1234}"

# Try auth first — if fails, create via CLI
if ! curl -sf -X POST "http://127.0.0.1:${PB_PORT}/api/collections/_superusers/auth-with-password" \
    -H "Content-Type: application/json" \
    -d "{\"identity\":\"${PB_ADMIN_EMAIL}\",\"password\":\"${PB_ADMIN_PASS}\"}" >/dev/null 2>&1; then
    echo "🔑 Creating superuser..."
    "$PB_BIN/pocketbase" superuser upsert "$PB_ADMIN_EMAIL" "$PB_ADMIN_PASS" --dir="$PB_DIR" 2>/dev/null
    echo "   ✅ Superuser created"
fi

# ── 7. Auto-setup database + import data ────────────────────────────────────
echo "🔧 Setting up database + importing company data..."

# Check for Gemini API key
if [ -z "$GEMINI_API_KEY" ]; then
    echo "   ⚠️  GEMINI_API_KEY not set — vector search will use fallback embeddings"
    echo "   💡 Set it: export GEMINI_API_KEY=your_key_here"
fi

python setup_db.py

# ── 7. Cleanup on exit ────────────────────────────────────────────────────
cleanup() {
    echo ""
    echo "🛑 Shutting down..."
    kill $PB_PID 2>/dev/null
    echo "   ✅ PocketBase stopped"
}
trap cleanup EXIT INT TERM

# ── 8. Start web server ────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════"
echo "  ✅ All systems go!"
echo "  🌐 Open http://localhost:${WEB_PORT}"
echo "  🗄️  PocketBase Admin: http://localhost:${PB_PORT}/_/"
echo "  🤖 MCP: claude mcp add creden-ai -- python mcp_server.py"
echo "════════════════════════════════════════════════"
echo ""
python app.py
