#!/bin/bash
# ── EC2 초기 설정 스크립트 ────────────────────────────────
# Ubuntu 22.04 LTS 기준
# 사용법: chmod +x ec2-setup.sh && sudo ./ec2-setup.sh

set -e

echo "🚀 모두의 마블 백엔드 — EC2 초기 설정 시작"

# ── 1. 시스템 업데이트 ─────────────────────────────────────
echo "📦 시스템 업데이트..."
apt-get update && apt-get upgrade -y

# ── 2. Docker 설치 ─────────────────────────────────────────
echo "🐳 Docker 설치..."
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Docker 권한 설정 (sudo 없이 사용 가능하도록)
usermod -aG docker ubuntu

# ── 3. Nginx 설치 (리버스 프록시) ──────────────────────────
echo "🌐 Nginx 설치..."
apt-get install -y nginx

# ── 4. Git 설치 ────────────────────────────────────────────
echo "📥 Git 설치..."
apt-get install -y git

# ── 5. 프로젝트 클론 ──────────────────────────────────────
echo "📂 프로젝트 클론..."
cd /home/ubuntu
if [ ! -d "modoo-marble-backend" ]; then
  sudo -u ubuntu git clone https://github.com/modoo-marble-team/modoo-marble-backend.git
  cd modoo-marble-backend
  sudo -u ubuntu git checkout develop
fi

# ── 6. 방화벽 설정 ────────────────────────────────────────
echo "🔒 방화벽 설정..."
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "✅ EC2 초기 설정 완료!"
echo ""
echo "📋 다음 단계:"
echo "  1. cd ~/modoo-marble-backend"
echo "  2. .env 파일 생성 (cp .env.example .env 후 편집)"
echo "  3. Nginx 설정: sudo cp deploy/nginx.conf /etc/nginx/sites-available/modoo-marble"
echo "  4. sudo ln -s /etc/nginx/sites-available/modoo-marble /etc/nginx/sites-enabled/"
echo "  5. sudo rm /etc/nginx/sites-enabled/default"
echo "  6. sudo nginx -t && sudo systemctl restart nginx"
echo "  7. docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build"
