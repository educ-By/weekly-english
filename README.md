# Weekly English — Backend Edition

每周自动收集 *The Economist / Guardian / BBC Learning English / NPR / Scientific American /
Smithsonian / Aeon / China Daily / Reader's Digest* 等适合高中生的英文阅读材料,按
CEFR B1 / B2 / C1 自动分级,**超纲词高亮**(基于真实高考词表+四六级词表)+ **DeepSeek
释义** + **AI 提问**。

提供三种部署形态:

- **本地 CLI**: `python run_weekly.py`
- **FastAPI 后端**: `python -m server.main`(默认端口 8000)
- **Render 免费层**: 推送代码 → 一键部署(零成本)

---

## 项目结构

```
weekly-english/
├── README.md
├── requirements.txt
├── render.yaml                  Render Blueprint
├── Procfile                     Render 启动命令
├── .env.example                 环境变量模板
│
├── core.py                      ⭐ 共享业务逻辑(抓取/分级/渲染)
├── run_weekly.py                本地 CLI
├── export_pdf.py                HTML → PDF
├── deepseek_client.py           ⭐ DeepSeek API 客户端(严格 system guard)
├── youdao_client.py             可选:有道词典接入位
│
├── fetchers/                    RSS / NewsAPI / HTML 抽取
├── pipeline/                    CEFR 难度 / 超纲词 / 摘要
├── templates/                   Jinja2 周报模板
├── static/                      样式 + 前端
│
├── data/
│   ├── cefr_vocab/              白名单(高考 + 四六级)
│   └── output/                  生成的周报 HTML
│
├── server/
│   └── main.py                  ⭐ FastAPI 后端 + APScheduler 定时
└── examples/                    离线样例
```

---

## 快速开始(本地)

### 1) 安装依赖

```bash
pip install -r requirements.txt
```

### 2) 离线渲染样例(不需要网络/Key)

```bash
python run_weekly.py --articles-json examples/sample_articles.json --out data/preview
```

打开 `data/preview/2026-W38/index.html`。

### 3) 启动后端(本地预览)

```bash
cp .env.example .env
# 编辑 .env,至少填入:
#   DEEPSEEK_API_KEY=sk-xxxxx
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/> 即可访问。
首次启动会在后台自动抓取并渲染当期内容(不阻塞首页)。

---

## 配置 DeepSeek

1. 注册 <https://platform.deepseek.com/>(海外) 或国内镜像
2. 创建 API Key
3. 填入 `.env`:
   ```
   DEEPSEEK_API_KEY=sk-xxxxx
   DEEPSEEK_MODEL=deepseek-flash   # DeepSeek-V4.1-Flash
   ```

### 严格回答范围

后端的 AI 服务(system prompt)被限制为:

**允许:**
1. 提问本周文章的词义、句子含义、背景
2. 英语学习方法、语法、词汇策略
3. 简短的文化/事实背景(仅当与本文相关时)

**拒绝**("This question is outside the scope of this weekly English tutor."):
- 闲聊、角色扮演、政治宗教哲学
- 代写作业、考试答案
- 与英语学习/本周文章无关的话题

---

## API 端点

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/` | GET | 最新一期主页(自动渲染,若无则后台拉取) |
| `/archive` | GET | 全部历史期号 |
| `/issue/{key}` | GET | 指定期号(如 `/issue/2026-W38`) |
| `/article/{issue_key}/{article_id}` | GET | 单篇精读页 |
| `/api/issues` | GET | JSON:全部期号列表 |
| `/api/dict?word=X&sentence=Y` | GET | DeepSeek 词典查询(返回 JSON) |
| `/api/ask` | POST | 提问 DeepSeek,自动带上下文 |
| `/api/admin/refresh` | POST | 手动触发本周抓取 |
| `/healthz` | GET | 健康检查(Render 用) |

### `/api/ask` 示例

```bash
curl -X POST http://localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What does flattened mean here?", "issue_key": "2026-W38"}'
```

返回:
```json
{
  "ok": true,
  "refused": false,
  "content": "In this article, ...",
  "model": "deepseek-flash"
}
```

### `/api/dict` 示例

```bash
curl "http://localhost:8000/api/dict?word=flattened&sentence=The+Phillips+curve+has+flattened"
```

返回:
```json
{
  "ok": true,
  "word": "flattened",
  "phonetic": "/ˈflætn̩d/",
  "definition_en": "having become flat or level",
  "translation": "(使)变平;(使)变弱;(使)失去锋芒",
  "examples": [...],
  "cefr_level": "B2"
}
```

---

## 部署到 Render(零成本)

### 一键 Blueprint 部署

1. 把代码推到 GitHub
2. 在 <https://render.com> 选择 **New + Blueprint**
3. 选你的仓库 — Render 自动识别 `render.yaml`
4. 在 dashboard 填 `DEEPSEEK_API_KEY`(secret 类型)
5. 点 Deploy — 几分钟后拿到一个 `xxx.onrender.com` URL

### 手动 Web Service 部署

1. Render → New → Web Service → 选仓库
2. Runtime: Python
3. Build command: `pip install --upgrade pip && pip install -r requirements.txt`
4. Start command: `uvicorn server.main:app --host 0.0.0.0 --port $PORT`
5. 加环境变量 `DEEPSEEK_API_KEY`
6. Plan 选 **Free**

### 免费层注意事项

- Render Free 在 15 分钟无活动后会 sleep,首次访问延迟约 30 秒
- APScheduler 在 sleep 时不运行,但下次访问会触发 startup hook 重抓一次
- 适合个人/小群体使用

---

## 数据来源(默认 23 个 RSS,并发抓取)

**新闻类(国内直连可达):** NPR News / NPR Education / NPR Science / CBS News /
CNBC / Sky News / France 24 / Global Times

**科学/科技:** MIT Technology Review / Nature News / New Scientist / ScienceDaily /
Phys.org / Ars Technica / TechCrunch / Engadget / Nautilus / Scientific American*

**文化/长文:** Smithsonian Magazine / Aeon / The Economist* / The Guardian* /
BBC Learning English*

带 `*` 的源在国内网络环境下通常需要代理(连接超时会自动跳过,不影响其他源;
配置代理后自动恢复)。原 China Daily(RSS 已下线)与 Reader's Digest(硬反爬)
已移除。

切换/新增 RSS:在 `core.DEFAULT_RSS_SOURCES` 中修改,或用 `--config my.json` 指定。
每期文章数上限 `core.full_refresh(max_articles=40)`。

---

## 词汇高亮 — 只标超纲

白名单来源(完全公开,非模型生成):

- 高中: <https://github.com/mahavivo/english-wordlists/blob/master/Highschool_edited.txt>
- 四六级: <https://github.com/mahavivo/english-wordlists/blob/master/CET_4+6_edited.txt>

判定规则:
1. 加载白名单后自动展开所有常见屈折/派生形(覆盖 `markets`/`became`/`held` 等)
2. 加入 200+ 不规则变化表兜底
3. 专有名词(首字母大写 / 全大写缩写 / 常见地名)排除
4. 不在白名单 → 标深紫色 + 下划线 + hover 时弹 DeepSeek 释义

---

## 自动化

- **APScheduler**:每周一 07:00(Asia/Shanghai)自动 `core.full_refresh()`
- **手动触发**: `curl -X POST https://your-app.onrender.com/api/admin/refresh`
- **本地 cron**:
  ```cron
  0 7 * * 1 cd /path/to/weekly-english && curl -X POST http://localhost:8000/api/admin/refresh
  ```

---

## 排错

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| `/` 卡死 | 首次抓 RSS 时网络慢 | 等待 ~30s,会自动完成;后台线程不阻塞 |
| `/api/dict` 一直返回错误 | 没填 `DEEPSEEK_API_KEY` | 编辑 `.env`,重启服务 |
| 主页空白 | 首次启动,后台抓取未完成 | 刷新几次或访问 `/api/admin/refresh` |
| APScheduler 不跑 | Render sleep | 正常,首次访问会触发 startup 重抓 |
| RSS 抓不到 | 站点临时屏蔽 | 检查 `/api/admin/refresh` 响应,临时在配置里移除失败源 |

---

## 开发路线图(已规划 / 已落地)

- ✅ 多 RSS 源聚合
- ✅ CEFR B1/B2/C1 自动分级
- ✅ 真实词表驱动的超纲词高亮(白名单 + 词形闭包)
- ✅ 主页/精读页/archive 三层 UI
- ✅ Web Speech 朗读 + 自定义音频上传
- ✅ DeepSeek 词典查询(端点式释义)
- ✅ DeepSeek 提问(严格 scope guard)
- ✅ APScheduler 周更
- ⏳ 用户登录 / 学习记录(可选)
- ⏳ 移动端 PWA(可选)

---

## License

代码:MIT。抓取到的文章版权归原出版方 — 请仅用于个人学习,不要大量转发。