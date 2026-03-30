#!/bin/sh
# Custom PocketBase + sqlite_vec entrypoint
# Start vectordb server, then auto-setup collections + import data

set -e

# Railway assigns PORT dynamically — use it, fallback to 8090
PB_PORT="${PORT:-8090}"

echo "🗄️  Creden AI — PocketBase + sqlite_vec Server (port ${PB_PORT})"

# Start vectordb (custom PocketBase) in background
./vectordb serve --http="0.0.0.0:${PB_PORT}" --dir=/pb_data &
PB_PID=$!

# Wait for PocketBase to be ready
echo "   ⏳ Waiting for PocketBase to start..."
for i in $(seq 1 30); do
    if wget -q --spider "http://127.0.0.1:${PB_PORT}/api/health" 2>/dev/null; then
        echo "   ✅ PocketBase is ready"
        break
    fi
    sleep 1
done

# Run auto-setup (create admin + collection + import data)
echo "   🔧 Running auto-setup..."
PB_PORT="${PB_PORT}" python3 /app/setup_collections.py
echo "   ✅ Setup complete"

# Bring PocketBase back to foreground
wait $PB_PID
