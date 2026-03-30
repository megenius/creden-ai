#!/bin/sh
# PocketBase entrypoint — start server, then auto-setup collections + import data

set -e

echo "🗄️  Creden AI — PocketBase Server"

# Start PocketBase in background
pocketbase serve --http="0.0.0.0:${PORT:-8090}" --dir=/pb_data &
PB_PID=$!

# Wait for PocketBase to be ready
echo "   ⏳ Waiting for PocketBase to start..."
for i in $(seq 1 30); do
    if wget -q --spider "http://127.0.0.1:${PORT:-8090}/api/health" 2>/dev/null; then
        echo "   ✅ PocketBase is ready"
        break
    fi
    sleep 1
done

# Run auto-setup (create admin + collection + import data)
echo "   🔧 Running auto-setup..."
python3 /app/setup_collections.py
echo "   ✅ Setup complete"

# Bring PocketBase back to foreground
wait $PB_PID
