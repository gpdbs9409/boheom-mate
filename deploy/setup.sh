#!/bin/bash
# EC2(Ubuntu 24.04)에서 한 번 실행한다. 코드, .env, chroma_db/는 미리 /home/ubuntu/boheom-mate에 올려 둔다.
set -euo pipefail
cd /home/ubuntu/boheom-mate

# 메모리 1GB 인스턴스에서 패키지 설치·형태소 분석기 로딩 중 멈추지 않도록 스왑 2GB를 만든다
if [ ! -f /swapfile ]; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip nginx

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

sudo cp deploy/boheom-mate.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now boheom-mate

sudo cp deploy/nginx.conf /etc/nginx/sites-available/boheom-mate
sudo ln -sf /etc/nginx/sites-available/boheom-mate /etc/nginx/sites-enabled/boheom-mate
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
