#!/bin/bash
# 맥에서 실행: 코드만 서버에 올린다. 약관 DB(chroma_db/), PDF(data/), 상태(state/), 질문 기록은 서버가 원본이라 덮어쓰지 않는다
# 집 네트워크에서만 된다(EC2 SSH는 집 IP만 허용)
set -euo pipefail
cd "$(dirname "$0")/.."
rsync -avz \
  --exclude .venv/ --exclude .git/ --exclude __pycache__/ --exclude .superpowers/ \
  --exclude .env --exclude chroma_db/ --exclude data/ --exclude state/ --exclude questions.db \
  -e "ssh -i $HOME/.ssh/boheom-mate.pem" \
  ./ ubuntu@3.36.241.30:/home/ubuntu/boheom-mate/
echo "올렸습니다. 패키지가 바뀌었으면 서버에서: .venv/bin/pip install -r requirements.txt && sudo systemctl restart boheom-mate"
