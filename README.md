# Weekly English — Backend Edition

每周自动收集 *The Economist / Guardian / BBC Learning English / NPR / Scientific American /
Smithsonian / Aeon / China Daily / Reader's Digest* 等适合高中生的英文阅读材料,按
CEFR B1 / B2 / C1 自动分级,**超纲词高亮**(基于高考 3500 词表 + 完整变形)+ **DeepSeek
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
│   ├── cefr_vocab/              白名单(高考 3500 + 变形;难度分级另用四六级)
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

页面(每个都有唯一 URL,顶部导航常驻):

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/` | GET | 首页门户:本期 hero + 精选 + 难度入口 + 最近往期(不是本期的副本) |
| `/archive` | GET | 往期列表,按年份分组 |
| `/issue/latest` | GET | 307 重定向到当前最新一期 |
| `/issue/{key}` | GET | 某一期的目录页(Contents + 本期词表汇总 + 分层卡片) |
| `/issue/{key}/article-{id}.html` | GET | 单篇精读页(面包屑 + 上下篇 + 相关阅读) |
| `/level/{B1\|B2\|C1}` | GET | 难度专区:跨所有期聚合该难度文章 |
| `/search?q=` | GET | 全站搜索,结果有自己的 URL |
| `/me` | GET | 我的学习页:生词本 / 阅读进度 / 阅读历史 |
| `/auth/login`·`/auth/register` | GET | 登录 / 注册 |
| `/{key}/index.html` 等旧路径 | GET | 兜底路由,历史链接不失效 |

接口:

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/api/issues` | GET | JSON:全部期号列表 |
| `/api/dict?word=X&sentence=Y` | GET | DeepSeek 词典查询(返回 JSON) |
| `/api/ask` | POST | 提问 DeepSeek,自动带上下文 |
| `/api/v1/vocab`·`/api/v1/progress`·`/api/v1/history` | GET/POST/DELETE | 生词本 / 阅读进度 / 阅读历史(需 Bearer token) |
| `/api/admin/refresh` | POST | 手动触发本周抓取 |
| `/healthz` | GET | 健康检查(Render 用) |

跨期页面(`/level`、`/search`、首页)读的是渲染期产出的索引:每期目录下有
`meta.json`,汇总成 `data/output/catalog.json`。改造前生成的旧期没有 `meta.json`,
服务启动时会就地从它们自己的 HTML 还原并重渲染一次(幂等),之后不再触发。

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

## 数据来源(默认 33 个 RSS,并发抓取)

**新闻类(国内直连可达):** NPR News / NPR Education / NPR Science / CBS News /
CNBC / Sky News / France 24 / Global Times

**科学/科技:** MIT Technology Review / Nature News / ScienceDaily /
Phys.org / Ars Technica / TechCrunch / Nautilus / Scientific American*

**文化/长文:** Smithsonian Magazine / Aeon / The Economist* / The Guardian* /
BBC Learning English*

带 `*` 的源在国内网络环境下通常需要代理(连接超时会自动跳过,不影响其他源;
配置代理后自动恢复)。原 China Daily RSS 已下线,改走 HTML 爬取;Reader's Digest
因硬反爬移除。

**The Guardian 双通道**:RSS 被墙,但官方 API(content.guardianapis.com)国内直连
可达 — 在 `.env` / 环境变量里配置 `GUARDIAN_API_KEY`(`open-platform.theguardian.com`
免费注册)即自动启用,返回官方全文。默认抓 `world`、`technology` 两个版块、每版块 2 篇
(`core.DEFAULT_GUARDIAN_SECTIONS`);未配置 key 则静默跳过,不影响其他源。

**代理**:requests 原生读取 `HTTPS_PROXY` 环境变量 — 本地挂代理后启动服务,
被墙的源(Economist/BBC/SciAm 等)会自动恢复抓取。

切换/新增 RSS:在 `core.DEFAULT_RSS_SOURCES` 中修改,或用 `--config my.json` 指定。
每期文章数上限 `core.full_refresh(max_articles=40)`。

---

## 词汇高亮 — 只标超纲

白名单来源(完全公开,非模型生成):

- 高考 3500: <https://github.com/mahavivo/english-wordlists/blob/master/Highschool_edited.txt>
- 基础词补丁: `data/cefr_vocab/basic_whitelist.txt` —— 高考表本身缺的超基础词(数词 two–ninety、thousand/million/billion、序数 third–hundredth、星期名、`night`/`solution`/`phone` 等),逐词核对后补录

判定规则:
1. 白名单 = 高考 3500 词表;**其完整变形**(复数/三单/过去式/过去分词/-ing/比较级/最高级/副词 -ly、缩写与所有格)由 `_lemmas()` 规则 + 不规则表在查词时归一覆盖
2. 不规则变化表兜底(不规则动词/不规则复数/不规则比较级)
3. 专有名词(首字母大写 / 全大写缩写 / 常见地名)排除
4. 不在白名单 → 标深紫色 + 下划线 + hover 时弹 DeepSeek 释义;也可**划词**选中任意单词/短语查翻译并加入生词本
5. 每篇最多标 30 个超纲词(按词频)

> 注:文章难度分级(pipeline/difficulty.py)仍按"高考 + 四六级"口径,与超纲词白名单相互独立。

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
- ✅ 高考 3500 词表驱动的超纲词高亮(白名单 + 完整变形 + 划词查词/加生词本)
- ✅ 多页刊物式信息架构:首页门户 / 本期目录 / 精读页 / 往期(按年分组)/ 难度专区 / 全站搜索 / 我的学习,共享常驻导航与面包屑
- ✅ Web Speech 朗读 + 自定义音频上传
- ✅ DeepSeek 词典查询(端点式释义)
- ✅ DeepSeek 提问(严格 scope guard)
- ✅ APScheduler 周更
- ⏳ 用户登录 / 学习记录(可选)
- ⏳ 移动端 PWA(可选)

---

## License

代码:MIT。抓取到的文章版权归原出版方 — 请仅用于个人学习,不要大量转发。