#!/bin/bash
# =============================================================================
# NEXUS ALPHA — VPS Deployment Script
# Run on the VPS as root:
#   curl -sL https://raw.githubusercontent.com/369network/nexus-alpha/main/deploy/vps_deploy.sh | bash
# Or after cloning:
#   bash deploy/vps_deploy.sh
# =============================================================================
set -euo pipefail

REPO_URL="https://github.com/369network/nexus-alpha.git"
APP_DIR="/opt/nexus-alpha"
BRANCH="main"

echo "============================================================"
echo "  NEXUS ALPHA — VPS Setup & Deploy"
echo "============================================================"

# 1. System update
echo "[1/8] Updating system packages..."
apt-get update -qq && apt-get upgrade -y -qq

# 2. Install Docker if not present
if ! command -v docker &>/dev/null; then
    echo "[2/8] Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
else
    echo "[2/8] Docker already installed ($(docker --version))"
fi

# 3. Install Docker Compose plugin if not present
if ! docker compose version &>/dev/null; then
    echo "[3/8] Installing Docker Compose plugin..."
    apt-get install -y docker-compose-plugin
else
    echo "[3/8] Docker Compose already installed"
fi

# 4. Clone / update repo
echo "[4/8] Cloning/updating repository..."
if [ -d "$APP_DIR/.git" ]; then
    cd "$APP_DIR"
    git fetch origin
    git reset --hard "origin/$BRANCH"
    git pull origin "$BRANCH"
else
    git clone --depth=1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi

# 5. Copy .env if not present
if [ ! -f "$APP_DIR/.env" ]; then
    echo "[5/8] WARNING: .env file not found at $APP_DIR/.env"
    echo "      Copy your .env file to $APP_DIR/.env before running the bot."
    echo "      Example: scp .env root@187.77.140.75:$APP_DIR/.env"
else
    echo "[5/8] .env file found."
fi

# 6. Build Docker image
echo "[6/8] Building Docker image (this may take a few minutes)..."
cd "$APP_DIR"
docker compose -f docker-compose.paper.yml build --no-cache

# 7. Stop old container if running
echo "[7/8] Stopping old container (if any)..."
docker compose -f docker-compose.paper.yml down --remove-orphans || true

# 8. Start fresh
echo "[8/8] Starting NEXUS ALPHA paper trading bot..."
docker compose -f docker-compose.paper.yml up -d

echo ""
echo "============================================================"
echo "  NEXUS ALPHA deployed successfully!"
echo "  Container: nexus-paper-bot"
echo "  Logs: docker logs -f nexus-paper-bot"
echo "  Stop: docker compose -f /opt/nexus-alpha/docker-compose.paper.yml down"
echo "============================================================"

# Show initial logs
echo ""
echo "Initial logs (ctrl+C to exit):"
docker logs -f nexus-paper-bot &
sleep 20
kill %1 2>/dev/null || true
