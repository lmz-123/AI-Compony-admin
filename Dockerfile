FROM python:3.12-slim

RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' /etc/apt/sources.list.d/debian.sources; \
      sed -i 's|http://security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources; \
    fi

RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY ai_company_admin/ ./ai_company_admin/
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -e .

ENV AI_COMPANY_ROOT=/ai-company \
    AI_COMPANY_STATE_DIR=/data/state \
    AI_COMPANY_CONFIG=/data/team-data/claudeteam.toml \
    AI_COMPANY_ADMIN_HOST=0.0.0.0 \
    AI_COMPANY_ADMIN_PORT=8766

CMD ["ai-company-admin"]
