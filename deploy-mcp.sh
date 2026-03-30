#!/bin/bash
# Deploy MCP SSE service to Railway
set -e

cp railway.toml railway.toml.bak 2>/dev/null || cp railway.json railway.json.bak 2>/dev/null || true
trap 'mv railway.toml.bak railway.toml 2>/dev/null || mv railway.json.bak railway.json 2>/dev/null || true' EXIT

cp railway.mcp.toml railway.toml
railway service link mcp-creden
railway up -d "$@"

echo "✅ MCP SSE deployed"
