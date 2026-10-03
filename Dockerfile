# Koyeb / 通用容器部署 — 应用从环境变量读 PORT(默认 8000)
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# 词表种子备份 — 挂载卷会覆盖 /app/data,启动时从这里恢复词表
RUN mkdir -p /opt/cefr_seed && cp -r data/cefr_vocab /opt/cefr_seed/cefr_vocab

EXPOSE 8000
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
