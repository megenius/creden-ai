FROM alpine:3.19

ARG PB_VERSION=0.25.9

# Install dependencies
RUN apk add --no-cache ca-certificates curl unzip python3

# Download PocketBase
RUN curl -fsSL -o /tmp/pocketbase.zip \
    "https://github.com/pocketbase/pocketbase/releases/download/v${PB_VERSION}/pocketbase_${PB_VERSION}_linux_amd64.zip" \
    && unzip /tmp/pocketbase.zip -d /usr/local/bin/ \
    && rm /tmp/pocketbase.zip \
    && chmod +x /usr/local/bin/pocketbase

# Setup data directory (will be mounted as volume)
RUN mkdir -p /pb_data

# Copy auto-setup + data import scripts
COPY setup_collections.py /app/setup_collections.py
COPY _all_companies.json /app/_all_companies.json
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8090

ENTRYPOINT ["/app/entrypoint.sh"]
