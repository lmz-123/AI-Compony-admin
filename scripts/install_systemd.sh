#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/opt/AI-Compony-admin}"
PY="${ROOT}/.venv/bin/python"

python3 -m venv "${ROOT}/.venv"
"${PY}" -m pip install --upgrade pip setuptools wheel
"${PY}" -m pip install -e "${ROOT}"

cat >/etc/systemd/system/ai-company-admin.service <<EOF
[Unit]
Description=AI Company standalone admin dashboard
After=network.target docker.service
Requires=docker.service

[Service]
WorkingDirectory=${ROOT}
Environment=AI_COMPANY_ROOT=/root/AI--compony
Environment=AI_COMPANY_STATE_DIR=/root/AI--compony/team-data/state
Environment=AI_COMPANY_CONFIG=/root/AI--compony/team-data/claudeteam.toml
Environment=AI_COMPANY_ADMIN_HOST=0.0.0.0
Environment=AI_COMPANY_ADMIN_PORT=8766
ExecStart=${PY} -m ai_company_admin.server
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now ai-company-admin
systemctl status ai-company-admin --no-pager
