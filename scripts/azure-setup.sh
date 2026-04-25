#!/usr/bin/env bash
# BITSM — Azure VM Provisioning Script
# Run as root on a fresh Ubuntu 24.04 LTS VM
# Usage: sudo bash scripts/azure-setup.sh

set -euo pipefail

DEPLOY_USER="deploy"
APP_DIR="/opt/bitsm"

echo "=== BITSM Azure VM Setup ==="
echo "Run as root on Ubuntu 24.04 LTS"
echo ""

# --- Timezone ---
timedatectl set-timezone UTC
echo "[+] Timezone: UTC"

# --- System update ---
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq
echo "[+] System updated"

# --- Essential packages ---
apt-get install -y --no-install-recommends \
    curl git ufw fail2ban ca-certificates gnupg lsb-release \
    apt-transport-https software-properties-common
echo "[+] Essential packages installed"

# --- Node.js 20 (for frontend builds) ---
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs
echo "[+] Node.js $(node --version) installed"

# --- Docker Engine (official repo — NOT snap) ---
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
    gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable docker
echo "[+] Docker $(docker --version) installed"

# --- cloudflared ---
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | \
    gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" \
    > /etc/apt/sources.list.d/cloudflared.list
apt-get update -qq
apt-get install -y cloudflared
echo "[+] cloudflared $(cloudflared --version) installed"

# --- Deploy user ---
if ! id "$DEPLOY_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$DEPLOY_USER"
fi
usermod -aG docker "$DEPLOY_USER"
mkdir -p /home/$DEPLOY_USER/.ssh
chmod 700 /home/$DEPLOY_USER/.ssh
touch /home/$DEPLOY_USER/.ssh/authorized_keys
chmod 600 /home/$DEPLOY_USER/.ssh/authorized_keys
chown -R $DEPLOY_USER:$DEPLOY_USER /home/$DEPLOY_USER/.ssh
echo "[+] Deploy user '$DEPLOY_USER' created (add SSH key to /home/$DEPLOY_USER/.ssh/authorized_keys)"

# --- App directory ---
mkdir -p "$APP_DIR"
chown $DEPLOY_USER:$DEPLOY_USER "$APP_DIR"
echo "[+] App directory: $APP_DIR"

# --- UFW firewall ---
# CF Tunnel is outbound-only — no inbound 80/443 needed
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment "SSH"
ufw --force enable
echo "[+] UFW enabled (SSH only inbound; CF Tunnel handles HTTP/HTTPS)"

# --- fail2ban ---
systemctl enable fail2ban
systemctl start fail2ban
echo "[+] fail2ban enabled"

# --- SSH hardening (key auth only) ---
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' /etc/ssh/sshd_config
systemctl restart sshd
echo "[+] SSH: password auth disabled"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Add your SSH public key:"
echo "     echo 'ssh-rsa AAAA...' >> /home/$DEPLOY_USER/.ssh/authorized_keys"
echo ""
echo "  2. Clone the repo:"
echo "     sudo -u $DEPLOY_USER git clone git@github.com:NovemberFalls/bitsm.git $APP_DIR"
echo ""
echo "  3. Set up .env:"
echo "     sudo -u $DEPLOY_USER cp $APP_DIR/.env.prod.template $APP_DIR/.env"
echo "     sudo -u $DEPLOY_USER nano $APP_DIR/.env"
echo ""
echo "  4. Set up CF Tunnel (run as deploy user):"
echo "     sudo -u $DEPLOY_USER cloudflared tunnel login"
echo "     sudo -u $DEPLOY_USER cloudflared tunnel create bitsm"
echo "     sudo -u $DEPLOY_USER cloudflared tunnel route dns bitsm bitsm.io"
echo "     # Then create /etc/cloudflared/config.yml (see CF_TUNNEL_SETUP.md)"
echo "     sudo cloudflared service install"
echo "     sudo systemctl enable --now cloudflared"
echo ""
echo "  5. First deploy:"
echo "     sudo -u $DEPLOY_USER bash -c 'cd $APP_DIR && cd webapp && npm ci && npm run build && cd .. && docker compose up -d --build'"
echo ""
echo "  6. Health check:"
echo "     curl http://localhost:5060/api/webhooks/health"
