#!/usr/bin/env bash
# =============================================================================
# NEXUS ALPHA - VPS Setup Script (Hostinger / Ubuntu 22.04 LTS)
# Run as root on a fresh server: bash deploy/hostinger/setup.sh
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()     { error "$*"; exit 1; }
section() { echo -e "\n${GREEN}═══ $* ═══${NC}"; }

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NEXUS_USER="nexus"
NEXUS_HOME="/home/${NEXUS_USER}"
NEXUS_DIR="${NEXUS_HOME}/nexus-alpha"
LOG_DIR="/var/log/nexus"
PYTHON_VERSION="3.12"
NODE_VERSION="20"
REDIS_VERSION="7"

# ---------------------------------------------------------------------------
# 1. System update and essential packages
# ---------------------------------------------------------------------------
section "System Update"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
apt-get install -y \
    build-essential \
    curl wget git unzip \
    software-properties-common \
    apt-transport-https \
    ca-certificates gnupg \
    lsb-release \
    htop iotop nethogs \
    net-tools \
    ufw \
    fail2ban \
    logrotate \
    jq \
    libssl-dev \
    zlib1g-dev \
    libbz2-dev \
    libreadline-dev \
    libsqlite3-dev \
    libncursesw5-dev \
    xz-utils \
    tk-dev \
    libxml2-dev \
    libxmlsec1-dev \
    libffi-dev \
    liblzma-dev

info "System packages installed."

# ---------------------------------------------------------------------------
# 2. Python 3.12
# ---------------------------------------------------------------------------
section "Python ${PYTHON_VERSION} Installation"
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -y
apt-get install -y \
    "python${PYTHON_VERSION}" \
    "python${PYTHON_VERSION}-dev" \
    "python${PYTHON_VERSION}-venv" \
    "python${PYTHON_VERSION}-distutils"

# Pip bootstrap
curl -sS https://bootstrap.pypa.io/get-pip.py | "python${PYTHON_VERSION}"
"python${PYTHON_VERSION}" -m pip install --upgrade pip setuptools wheel

# Create symlinks
update-alternatives --install /usr/bin/python3 python3 "/usr/bin/python${PYTHON_VERSION}" 1
update-alternatives --install /usr/bin/python python "/usr/bin/python${PYTHON_VERSION}" 1

info "Python $(python --version) ready."

# ---------------------------------------------------------------------------
# 3. Poetry
# ---------------------------------------------------------------------------
section "Poetry Installation"
curl -sSL https://install.python-poetry.org | python -
ln -sf "${HOME}/.local/bin/poetry" /usr/local/bin/poetry
poetry config virtualenvs.in-project true
info "Poetry $(poetry --version) ready."

# ---------------------------------------------------------------------------
# 4. TA-Lib C library
# ---------------------------------------------------------------------------
section "TA-Lib C Library"
TALIB_VERSION="0.4.0"
TALIB_TAR="ta-lib-${TALIB_VERSION}-src.tar.gz"
TALIB_URL="https://prdownloads.sourceforge.net/ta-lib/${TALIB_TAR}"

cd /tmp
if [ ! -f "${TALIB_TAR}" ]; then
    wget -q "${TALIB_URL}"
fi
tar -xzf "${TALIB_TAR}"
cd "ta-lib"
./configure --prefix=/usr
make -j"$(nproc)"
make install
ldconfig
cd /
rm -rf /tmp/ta-lib /tmp/"${TALIB_TAR}"
info "TA-Lib C library installed."

# ---------------------------------------------------------------------------
# 5. Docker + docker-compose
# ---------------------------------------------------------------------------
section "Docker Installation"
if ! command -v docker &>/dev/null; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] \
        https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable docker
    systemctl start docker
fi
info "Docker $(docker --version | cut -d' ' -f3 | tr -d ,) ready."

# ---------------------------------------------------------------------------
# 6. Node.js 20
# ---------------------------------------------------------------------------
section "Node.js ${NODE_VERSION} Installation"
if ! command -v node &>/dev/null; then
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_VERSION}.x" | bash -
    apt-get install -y nodejs
fi
info "Node.js $(node --version) ready."

# ---------------------------------------------------------------------------
# 7. Redis
# ---------------------------------------------------------------------------
section "Redis Installation"
if ! command -v redis-server &>/dev/null; then
    apt-get install -y redis-server
    sed -i 's/^supervised no/supervised systemd/' /etc/redis/redis.conf
    sed -i 's/^# maxmemory-policy noeviction/maxmemory-policy allkeys-lru/' /etc/redis/redis.conf
    sed -i 's/^# maxmemory <bytes>/maxmemory 512mb/' /etc/redis/redis.conf
    systemctl enable redis-server
    systemctl start redis-server
fi
info "Redis $(redis-server --version | cut -d' ' -f3 | cut -d= -f2) ready."

# ---------------------------------------------------------------------------
# 8. Nginx
# ---------------------------------------------------------------------------
section "Nginx Installation"
if ! command -v nginx &>/dev/null; then
    apt-get install -y nginx
    systemctl enable nginx
fi

# Basic reverse proxy config for Nexus dashboard API
cat > /etc/nginx/sites-available/nexus <<'NGINX_CONF'
server {
    listen 80;
    server_name _;

    # Health check endpoint
    location /health {
        return 200 'OK';
        add_header Content-Type text/plain;
    }

    # Dashboard API proxy (if running locally)
    location /api/ {
        proxy_pass         http://127.0.0.1:8000/;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection 'upgrade';
        proxy_set_header   Host $host;
        proxy_cache_bypass $http_upgrade;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 60s;
    }
}
NGINX_CONF

ln -sf /etc/nginx/sites-available/nexus /etc/nginx/sites-enabled/nexus
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
info "Nginx configured."

# ---------------------------------------------------------------------------
# 9. Certbot for HTTPS
# ---------------------------------------------------------------------------
section "Certbot Installation"
if ! command -v certbot &>/dev/null; then
    snap install --classic certbot
    ln -sf /snap/bin/certbot /usr/bin/certbot
fi
info "Certbot ready (run certbot --nginx to obtain certificate)."

# ---------------------------------------------------------------------------
# 10. Create nexus user with limited permissions
# ---------------------------------------------------------------------------
section "Nexus User Setup"
if ! id "${NEXUS_USER}" &>/dev/null; then
    useradd -m -s /bin/bash -d "${NEXUS_HOME}" "${NEXUS_USER}"
    usermod -aG docker "${NEXUS_USER}"
    info "Created user '${NEXUS_USER}'."
else
    warn "User '${NEXUS_USER}' already exists."
fi

# ---------------------------------------------------------------------------
# 11. Project directory and permissions
# ---------------------------------------------------------------------------
section "Project Directory"
mkdir -p "${NEXUS_DIR}"
mkdir -p "${LOG_DIR}"
chown -R "${NEXUS_USER}:${NEXUS_USER}" "${NEXUS_HOME}"
chown -R "${NEXUS_USER}:${NEXUS_USER}" "${LOG_DIR}"
chmod 755 "${NEXUS_DIR}"
chmod 750 "${LOG_DIR}"
info "Directories created: ${NEXUS_DIR}, ${LOG_DIR}"

# ---------------------------------------------------------------------------
# 12. Install Python dependencies via Poetry (placeholder)
# ---------------------------------------------------------------------------
section "Python Dependencies"
if [ -f "${NEXUS_DIR}/pyproject.toml" ]; then
    cd "${NEXUS_DIR}"
    sudo -u "${NEXUS_USER}" poetry install --no-dev
    info "Python dependencies installed."
else
    warn "pyproject.toml not found in ${NEXUS_DIR} — run 'poetry install' after deploying code."
fi

# ---------------------------------------------------------------------------
# 13. Log rotation
# ---------------------------------------------------------------------------
section "Log Rotation"
cat > /etc/logrotate.d/nexus-alpha <<'LOGROTATE'
/var/log/nexus/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0640 nexus nexus
    sharedscripts
    postrotate
        systemctl kill -s USR1 nexus-bot.service 2>/dev/null || true
    endscript
}
LOGROTATE
info "Log rotation configured."

# ---------------------------------------------------------------------------
# 14. Unattended upgrades (security patches)
# ---------------------------------------------------------------------------
section "Unattended Upgrades"
apt-get install -y unattended-upgrades
cat > /etc/apt/apt.conf.d/50unattended-upgrades <<'UA_CONF'
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}";
    "${distro_id}:${distro_codename}-security";
};
Unattended-Upgrade::AutoFixInterruptedDpkg "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
UA_CONF

cat > /etc/apt/apt.conf.d/20auto-upgrades <<'UA_AUTO'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
UA_AUTO
info "Unattended upgrades configured."

# ---------------------------------------------------------------------------
# 15. Firewall
# ---------------------------------------------------------------------------
section "Firewall (UFW)"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
info "Firewall configured."

# ---------------------------------------------------------------------------
# 16. fail2ban
# ---------------------------------------------------------------------------
section "fail2ban"
systemctl enable fail2ban
systemctl start fail2ban
info "fail2ban active."

# ---------------------------------------------------------------------------
# 17. Systemd services
# ---------------------------------------------------------------------------
section "Systemd Services"
DEPLOY_DIR="$(dirname "$(realpath "$0")")"

if [ -f "${DEPLOY_DIR}/nexus-bot.service" ]; then
    sed "s|NEXUS_DIR|${NEXUS_DIR}|g; s|NEXUS_USER|${NEXUS_USER}|g" \
        "${DEPLOY_DIR}/nexus-bot.service" > /etc/systemd/system/nexus-bot.service
    info "nexus-bot.service installed."
fi

if [ -f "${DEPLOY_DIR}/nexus-data.service" ]; then
    sed "s|NEXUS_DIR|${NEXUS_DIR}|g; s|NEXUS_USER|${NEXUS_USER}|g" \
        "${DEPLOY_DIR}/nexus-data.service" > /etc/systemd/system/nexus-data.service
    info "nexus-data.service installed."
fi

systemctl daemon-reload

# ---------------------------------------------------------------------------
# 18. Cron for health monitoring
# ---------------------------------------------------------------------------
section "Health Monitor Cron"
if [ -f "${DEPLOY_DIR}/monitoring.sh" ]; then
    cp "${DEPLOY_DIR}/monitoring.sh" /usr/local/bin/nexus-monitor.sh
    chmod +x /usr/local/bin/nexus-monitor.sh

    (crontab -l 2>/dev/null; echo "*/5 * * * * /usr/local/bin/nexus-monitor.sh >> ${LOG_DIR}/monitor.log 2>&1") \
        | crontab -
    info "Health monitor cron installed (every 5 minutes)."
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
section "Setup Complete"
echo ""
echo "  Next steps:"
echo "  1. Copy your project to ${NEXUS_DIR}/"
echo "     scp -r . ${NEXUS_USER}@<server_ip>:${NEXUS_DIR}/"
echo ""
echo "  2. Create .env file:"
echo "     cp ${NEXUS_DIR}/.env.example ${NEXUS_DIR}/.env"
echo "     nano ${NEXUS_DIR}/.env"
echo ""
echo "  3. Install Python dependencies:"
echo "     cd ${NEXUS_DIR} && sudo -u ${NEXUS_USER} poetry install"
echo ""
echo "  4. Set up Supabase schema:"
echo "     psql \$DATABASE_URL -f ${NEXUS_DIR}/scripts/setup_supabase.sql"
echo ""
echo "  5. Seed historical data:"
echo "     sudo -u ${NEXUS_USER} python ${NEXUS_DIR}/scripts/seed_historical.py"
echo ""
echo "  6. (Optional) Get HTTPS cert:"
echo "     certbot --nginx -d your-domain.com"
echo ""
echo "  7. Start the bot:"
echo "     systemctl start nexus-bot.service nexus-data.service"
echo "     systemctl enable nexus-bot.service nexus-data.service"
echo ""
info "VPS setup finished successfully."
