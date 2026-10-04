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

# 历史期种子 — 周更只产当期,旧的抓不回来;卷被重建(或从没拿到过)时
# 由启动逻辑把这批渲染成品补进 out_dir,否则 archive 里永远只有当期。
RUN mkdir -p /opt/issues_seed && cp -r data/archive_seed/. /opt/issues_seed/

# 离线词典种子 — 卷会盖住 /app/data,把压缩源备到 /opt;sqlite 索引不随镜像发,
# 由启动逻辑从 gz 现建(比 gz 大 3 倍,没必要进仓库/镜像)
RUN mkdir -p /opt/dict_seed && cp data/dict/ecdict.csv.gz /opt/dict_seed/

EXPOSE 8000
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
