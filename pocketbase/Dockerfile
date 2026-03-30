# ---- Build stage: custom PocketBase + sqlite_vec ----
FROM golang:1.25-alpine AS builder

# GCC + musl-dev required for CGO (sqlite-vec compiles C source)
RUN apk add --no-cache gcc musl-dev

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download

# sqlite-vec needs sqlite3.h but go-sqlite3 bundles it as sqlite3-binding.h
# Copy it as sqlite3.h into the sqlite-vec module directory
RUN VEC_DIR=/go/pkg/mod/github.com/asg017/sqlite-vec-go-bindings@v0.1.6/cgo && \
    SQLITE3_DIR=/go/pkg/mod/github.com/mattn/go-sqlite3@v1.14.37 && \
    chmod -R u+w "$VEC_DIR" && \
    cp "$SQLITE3_DIR/sqlite3-binding.h" "$VEC_DIR/sqlite3.h" && \
    sed -i 's/^typedef u_int[0-9]*_t uint[0-9]*_t;/\/* patched for musl *\//' "$VEC_DIR/sqlite-vec.c" && \
    sed -i 's/^typedef int8_t int8_t;/\/* patched *\//' "$VEC_DIR/sqlite-vec.c"

COPY *.go ./

# CGO_ENABLED=1 is mandatory — sqlite-vec is a C extension
RUN CGO_ENABLED=1 GOOS=linux \
    CGO_CFLAGS="-Wno-incompatible-pointer-types" \
    go build -tags "fts5" -ldflags="-s -w" -o /vectordb .

# ---- Runtime stage ----
FROM alpine:3.20

RUN apk add --no-cache ca-certificates python3

WORKDIR /app
COPY --from=builder /vectordb .

RUN mkdir -p /pb_data

COPY setup_collections.py /app/setup_collections.py
COPY _all_companies.json /app/_all_companies.json
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8090

ENTRYPOINT ["/app/entrypoint.sh"]
