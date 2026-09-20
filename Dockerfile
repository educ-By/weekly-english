# Koyeb / 通用容器部署 — 应用从环境变量读 PORT(默认 8000)
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
