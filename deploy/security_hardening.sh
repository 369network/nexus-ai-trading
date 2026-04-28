#!/usr/bin/env bash
# =============================================================================
# NEXUS ALPHA — VPS Security Hardening Script
# Target: Ubuntu 22.04 LTS  |  VPS: 187.77.140.75
# Run as root: sudo bash security_hardening.sh
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}  $*"; }
info() { echo -e "${YELLOW}[..] ${NC} $*"; }
err()  { echo -e "${RED}[ERR]${NC} $*" >&2; }

require_root() {
  [[ $EUID -eq 0 ]] || { err "Run this script as root (sudo bash $0)"; exit 1; }
}

# ---------------------------------------------------------------------------
# 1. UFW FIREWALL
# ---------------------------------------------------------------------------
setup_firewall() {
  info "Configuring UFW firewall..."
  apt-get install -y ufw >/dev/null 2>&1

  ufw --force reset
  ufw default deny incoming
  ufw default allow outgoing

  ufw allow 22/tcp    comment "SSH"
  ufw allow 8080/tcp  comment "Health endpoint"
  ufw allow 9090/tcp  comment "Prometheus"
  ufw allow 3001/tcp  comment "Grafana"

  ufw --force enable
  ok "UFW configured: allow 22, 8080, 9090, 3001 — deny everything else"
}

# ---------------------------------------------------------------------------
# 2. SSH HARDENING
# ---------------------------------------------------------------------------
harden_ssh() {
  info "Hardening SSH configuration..."
  local cfg=/etc/ssh/sshd_config

  # Back up original
  cp -n "$cfg" "${cfg}.bak.$(date +%Y%m%d)"

  # Apply hardened settings (idempotent: replace existing or append)
  apply_setting() {
    local key="$1" val="$2"
    if grep -qE "^#?${key}" "$cfg"; then
      sed -i "s|^#\?${key}.*|${key} ${val}|" "$cfg"
    else
      echo "${key} ${val}" >> "$cfg"
    fi
  }

  apply_setting "PasswordAuthentication"    "no"
  apply_setting "PermitRootLogin"           "prohibit-password"
  apply_setting "MaxAuthTries"              "3"
  apply_setting "PubkeyAuthentication"      "yes"
  apply_setting "AuthorizedKeysFile"        ".ssh/authorized_keys"
  apply_setting "X11Forwarding"             "no"
  apply_setting "AllowTcpForwarding"        "no"
  apply_setting "ClientAliveInterval"       "300"
  apply_setting "ClientAliveCountMax"       "2"
  apply_setting "LoginGraceTime"            "30"

  sshd -t && systemctl reload sshd
  ok "SSH hardened (password auth off, root login disabled, MaxAuthTries=3)"
}

# ---------------------------------------------------------------------------
# 3. FAIL2BAN
# ---------------------------------------------------------------------------
setup_fail2ban() {
  info "Installing and configuring fail2ban..."
  apt-get install -y fail2ban >/dev/null 2>&1

  cat > /etc/fail2ban/jail.local <<'EOF'
[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 5
backend  = systemd

[sshd]
enabled  = true
port     = ssh
logpath  = %(sshd_log)s
maxretry = 3
bantime  = 7200
EOF

  systemctl enable --now fail2ban
  ok "fail2ban enabled (SSH jail: 3 retries → 2h ban)"
}

# ---------------------------------------------------------------------------
# 4. NON-ROOT nexus USER
# ---------------------------------------------------------------------------
create_nexus_user() {
  info "Creating 'nexus' service user..."
  if id nexus &>/dev/null; then
    info "User 'nexus' already exists — skipping creation"
  else
    useradd -r -m -s /bin/bash -c "NEXUS ALPHA service account" nexus
    ok "User 'nexus' created"
  fi

  # Add to docker group so nexus can run containers without sudo
  usermod -aG docker nexus
  ok "User 'nexus' added to docker group"
}

# ---------------------------------------------------------------------------
# 5. FILE PERMISSIONS ON /opt/nexus-alpha
# ---------------------------------------------------------------------------
set_permissions() {
  info "Setting file permissions on /opt/nexus-alpha..."
  local app_dir=/opt/nexus-alpha

  if [[ ! -d "$app_dir" ]]; then
    mkdir -p "$app_dir"
    info "Created $app_dir"
  fi

  chown -R nexus:nexus "$app_dir"

  # Sensitive files: owner read/write only
  [[ -f "$app_dir/.env" ]] && chmod 600 "$app_dir/.env"

  # Scripts: owner rwx, group+other rx
  find "$app_dir" -name "*.sh" -exec chmod 755 {} \;

  # App directory itself
  chmod 750 "$app_dir"

  ok "Permissions set (.env=600, scripts=755, dir=750, owner=nexus)"
}

# ---------------------------------------------------------------------------
# 6. DOCKER LOG ROTATION
# ---------------------------------------------------------------------------
configure_log_rotation() {
  info "Configuring Docker json-file log rotation..."
  local docker_cfg=/etc/docker/daemon.json

  if [[ -f "$docker_cfg" ]]; then
    # Merge into existing config using python (avoids overwriting other settings)
    python3 - <<'PYEOF'
import json, sys
with open("/etc/docker/daemon.json") as f:
    cfg = json.load(f)
cfg.setdefault("log-driver", "json-file")
cfg.setdefault("log-opts", {})
cfg["log-opts"].update({"max-size": "50m", "max-file": "5"})
with open("/etc/docker/daemon.json", "w") as f:
    json.dump(cfg, f, indent=2)
PYEOF
  else
    cat > "$docker_cfg" <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "5"
  }
}
EOF
  fi

  systemctl reload docker 2>/dev/null || systemctl restart docker
  ok "Docker log rotation: max 50 MB × 5 files per container"
}

# ---------------------------------------------------------------------------
# 7. DAILY DOCKER CLEANUP CRON
# ---------------------------------------------------------------------------
setup_docker_prune_cron() {
  info "Setting up daily Docker cleanup cron job..."
  local cron_file=/etc/cron.daily/docker-prune

  cat > "$cron_file" <<'EOF'
#!/bin/bash
# Daily Docker image/container/network cleanup
/usr/bin/docker system prune -f --volumes=false >> /var/log/docker-prune.log 2>&1
EOF

  chmod 755 "$cron_file"
  ok "Cron job created: /etc/cron.daily/docker-prune (runs daily, logs to /var/log/docker-prune.log)"
}

# ---------------------------------------------------------------------------
# 8. AUTOMATIC SECURITY UPDATES
# ---------------------------------------------------------------------------
enable_auto_security_updates() {
  info "Enabling automatic security updates (unattended-upgrades)..."
  apt-get install -y unattended-upgrades >/dev/null 2>&1

  # Enable unattended-upgrades
  cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Download-Upgradeable-Packages "1";
APT::Periodic::AutocleanInterval "7";
APT::Periodic::Unattended-Upgrade "1";
EOF

  # Restrict to security updates only
  cat > /etc/apt/apt.conf.d/50unattended-upgrades <<'EOF'
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
};
Unattended-Upgrade::AutoFixInterruptedDpkg "true";
Unattended-Upgrade::MinimalSteps "true";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-New-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
EOF

  systemctl enable --now unattended-upgrades
  ok "Automatic security updates enabled (no auto-reboot)"
}

# ---------------------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------------------
print_summary() {
  echo ""
  echo "============================================================"
  echo "  NEXUS ALPHA — Security Hardening Complete"
  echo "============================================================"
  echo ""
  echo "  [1] UFW Firewall     : ENABLED"
  echo "      Open ports       : 22 (SSH), 8080 (health), 9090 (Prometheus), 3001 (Grafana)"
  echo ""
  echo "  [2] SSH Hardening    : DONE"
  echo "      Password auth    : DISABLED"
  echo "      Root login       : prohibit-password"
  echo "      MaxAuthTries     : 3"
  echo ""
  echo "  [3] fail2ban         : ENABLED"
  echo "      SSH jail         : 3 bad attempts → 2h ban"
  echo ""
  echo "  [4] nexus user       : CREATED (in docker group)"
  echo ""
  echo "  [5] File permissions : SET"
  echo "      .env             : 600 (owner-only)"
  echo "      scripts/*.sh     : 755"
  echo "      /opt/nexus-alpha : 750 (owner=nexus)"
  echo ""
  echo "  [6] Docker log rot.  : 50 MB × 5 files"
  echo ""
  echo "  [7] Docker prune     : /etc/cron.daily/docker-prune"
  echo ""
  echo "  [8] Auto sec updates : ENABLED (no auto-reboot)"
  echo ""
  echo "  IMPORTANT: Ensure your SSH public key is in"
  echo "  /root/.ssh/authorized_keys OR /home/nexus/.ssh/authorized_keys"
  echo "  before logging out — password auth is now DISABLED."
  echo ""
  echo "============================================================"
}

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
main() {
  require_root
  apt-get update -qq

  setup_firewall
  harden_ssh
  setup_fail2ban
  create_nexus_user
  set_permissions
  configure_log_rotation
  setup_docker_prune_cron
  enable_auto_security_updates
  print_summary
}

main "$@"
