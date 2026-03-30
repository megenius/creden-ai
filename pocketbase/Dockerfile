# ---- Build stage: custom PocketBase + sqlite_vec ----
FROM golang:1.25-alpine AS builder

# GCC + musl-dev required for CGO (sqlite-vec compiles C source)
RUN apk add --no-cache gcc musl-dev

WORKDIR /src
# Copy vectordb Go source from the pocketbase/ context
COPY go.mod go.sum ./
RUN go mod download

COPY *.go ./

# CGO_ENABLED=1 is mandatory — sqlite-vec is a C extension
RUN CGO_ENABLED=1 GOOS=linux \
    go build -tags "fts5" -ldflags="-s -w" -o /vectordb .

# ---- Runtime stage ----
FROM alpine:3.20

RUN apk add --no-cache ca-certificates python3

WORKDIR /app
COPY --from=builder /vectordb .

# Setup data directory (will be mounted as volume)
RUN mkdir -p /pb_data

# Copy auto-setup + data import scripts
COPY setup_collections.py /app/setup_collections.py
COPY _all_companies.json /app/_all_companies.json
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8090

ENTRYPOINT ["/app/entrypoint.sh"]
