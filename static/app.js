/* ------------------------------------------------------------------
   Weekly English — 前端交互
   - 主页:搜索(标题/正文) + 难度 + 来源筛选
   - 精读页:生词高亮、Web Speech 朗读、上传自定义音频
   - 全部用 SVG 图标,不使用 emoji
   ------------------------------------------------------------------ */

(() => {
  "use strict";

  /* ---------------- 全局导航(窄屏折叠) ---------------- */
  const page = document.body.dataset.page;

  function bindNav() {
    const burger = document.querySelector(".nav-burger");
    const nav = document.getElementById("site-nav");
    if (!burger || !nav) return;
    burger.addEventListener("click", () => {
      const open = nav.classList.toggle("is-open");
      burger.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  /* ---------------- 折叠层的高度过渡 ----------------
     CSS 只定义"折叠后长什么样"(.is-closed → height:0 + padding:0 + overflow:hidden);
     这里负责切换瞬间把高度钉成像素值,让 height 可插值 —— 直接 height:auto ↔ 0 是不过渡的。
     打不动的场景(无 JS / 减少动态效果 / 筛选联动)就走非动画分支,直接落到 CSS 状态。 */
  const prefersReducedMotion = () =>
    !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const collapseTokens = new WeakMap();   // body → 当前动画令牌,用于作废过期回调

  // 量出"展开时"的自然高度:临时解除折叠量一次,同步还原,不会产生可见闪烁
  function naturalHeight(block, body) {
    const inlineH = body.style.height;
    const wasClosed = block.classList.contains("is-closed");
    body.style.height = "";
    block.classList.remove("is-closed");
    const h = body.getBoundingClientRect().height;
    body.style.height = inlineH;
    if (wasClosed) block.classList.add("is-closed");
    return h;
  }

  function setCollapsed(block, collapsed, animate) {
    const body = block.querySelector(".block-body");
    if (!body) return;

    if (!animate || prefersReducedMotion()) {
      collapseTokens.set(body, Symbol());     // 作废进行中的动画,免得它的收尾把状态清掉
      body.style.height = "";
      block.classList.toggle("is-closed", collapsed);
      return;
    }

    const token = Symbol();
    collapseTokens.set(body, token);

    const from = body.getBoundingClientRect().height;
    const to = collapsed ? 0 : naturalHeight(block, body);

    body.style.height = from + "px";           // 钉住起始值
    void body.offsetHeight;                    // 强制重排,否则起止值会被合并成一次样式变更
    block.classList.toggle("is-closed", collapsed);
    body.style.height = to + "px";

    const done = (e) => {
      if (e && (e.target !== body || e.propertyName !== "height")) return;
      body.removeEventListener("transitionend", done);
      if (collapseTokens.get(body) !== token) return;
      body.style.height = "";                  // 交还 auto,窗口尺寸变化时还能自适应
    };
    body.addEventListener("transitionend", done);
    setTimeout(done, 400);                     // transitionend 万一不来也要收尾
  }

  /* ---------------- 本期页筛选 ---------------- */
  function bindIndex() {
    const cards = Array.from(document.querySelectorAll(".card"));
    const status = document.getElementById("status");
    const empty = document.getElementById("empty");
    if (!cards.length) return;

    const search = document.getElementById("q");
    const params = new URLSearchParams(location.search);
    let level = (params.get("level") || "all").toUpperCase();
    if (!["B1", "B2", "C1"].includes(level)) level = "all";
    let source = "all";

    if (search && params.get("q")) search.value = params.get("q");

    // 把当前筛选写回地址栏 —— 结果可分享、可回退,而不是一次性的页面内状态
    let urlTimer = null;
    function syncUrl() {
      clearTimeout(urlTimer);
      urlTimer = setTimeout(() => {
        const p = new URLSearchParams(location.search);
        const q = (search?.value || "").trim();
        if (q) p.set("q", q); else p.delete("q");
        if (level !== "all") p.set("level", level); else p.delete("level");
        const qs = p.toString();
        history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
      }, 250);
    }

    function apply() {
      const q = (search?.value || "").trim().toLowerCase();
      const filtering = !!q || level !== "all" || source !== "all";
      let n = 0;
      const blockVisible = {};
      cards.forEach(card => {
        const title = (card.dataset.title || "").toLowerCase();
        const body = (card.dataset.body || "").toLowerCase();
        const lvl = card.dataset.level || "";
        const src = card.dataset.source || "";
        const matchQ = !q || title.includes(q) || body.includes(q);
        const matchL = level === "all" || lvl === level;
        const matchS = source === "all" || src === source;
        const show = matchQ && matchL && matchS;
        card.style.display = show ? "" : "none";
        if (show) { n += 1; blockVisible[lvl] = (blockVisible[lvl] || 0) + 1; }
      });
      // 分层联动:筛选/搜索时自动展开所有层,没有命中内容的层整层隐藏
      // 不带动画 —— 边打字边展开会拖慢手感
      document.querySelectorAll(".level-block").forEach(block => {
        if (filtering) setCollapsed(block, false, false);
        const lvl = block.dataset.block || "";
        block.hidden = filtering && !!lvl && !blockVisible[lvl];
      });
      if (status) status.textContent = `Showing ${n} of ${cards.length}`;
      if (empty) empty.hidden = n !== 0;
      syncUrl();
    }

    // 层标题点击 = 折叠/展开该层
    document.querySelectorAll("[data-toggle-block]").forEach(head => {
      head.addEventListener("click", () => {
        const block = head.closest(".level-block");
        if (!block) return;
        setCollapsed(block, !block.classList.contains("is-closed"), true);
      });
    });

    // 从文章页返回(bfcache 恢复)时重新同步分层状态
    window.addEventListener("pageshow", (e) => {
      if (e.persisted) apply();
    });

    document.querySelectorAll('.filter-group[data-filter="level"] .chip')
      .forEach(btn => btn.addEventListener("click", () => {
        document.querySelectorAll('.filter-group[data-filter="level"] .chip')
          .forEach(b => b.classList.remove("is-on"));
        btn.classList.add("is-on");
        level = btn.dataset.value;
        apply();
      }));

    document.querySelectorAll('.filter-group[data-filter="source"] .chip')
      .forEach(btn => btn.addEventListener("click", () => {
        document.querySelectorAll('.filter-group[data-filter="source"] .chip')
          .forEach(b => b.classList.remove("is-on"));
        btn.classList.add("is-on");
        source = btn.dataset.value;
        apply();
      }));

    // 地址栏里的 level 参数要反映到 chip 高亮上
    document.querySelectorAll('.filter-group[data-filter="level"] .chip')
      .forEach(b => b.classList.toggle("is-on",
        (b.dataset.value || "").toUpperCase() === level));

    search?.addEventListener("input", apply);
    apply();
  }

  /* ---------------- 精读页:生词高亮 ---------------- */
  function highlightRare() {
    const article = document.querySelector("article.entry");
    if (!article) return;
    // 从 vocab 列表收集词
    const words = new Set();
    article.querySelectorAll(".vocab-word").forEach(el => {
      const w = el.textContent.trim().toLowerCase();
      if (w) words.add(w);
    });
    if (!words.size) return;

    const esc = Array.from(words)
      .map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
      .sort((a, b) => b.length - a.length)   // 长词优先
      .join("|");
    const re = new RegExp("\\b(" + esc + ")\\b", "gi");

    const body = article.querySelector(".entry-body");
    if (!body) return;
    body.querySelectorAll("p").forEach(p => {
      if (p.dataset.highlighted === "1") return;
      p.innerHTML = p.innerHTML.replace(re, m =>
        `<span class="rare" data-word="${m.toLowerCase()}" title="Out-of-scope · click for gloss">${m}</span>`
      );
      p.dataset.highlighted = "1";
    });
  }

  /* ---------------- 精读页:词 hover popup ---------------- */
  function bindVocabPopup() {
    const article = document.querySelector("article.entry");
    if (!article) return;

    const popup = document.createElement("div");
    popup.className = "vocab-popup";
    popup.hidden = true;
    document.body.appendChild(popup);

    const cache = new Map();   // word → lookup promise
    let active = null;

    function showAt(el, text) {
      const r = el.getBoundingClientRect();
      popup.innerHTML = text;
      popup.hidden = false;
      const pw = popup.offsetWidth;
      const ph = popup.offsetHeight;
      let x = r.left + r.width / 2 - pw / 2;
      let y = r.top - ph - 4;   // 紧贴单词,不留缝隙,鼠标移过去不会穿过空档
      if (y < 8) y = r.bottom + 4;
      x = Math.max(8, Math.min(window.innerWidth - pw - 8, x));
      popup.style.left = x + "px";
      popup.style.top = y + "px";
      // 弹窗复用同一个 DOM,只改 left/top —— 不重放动画的话,连续悬停时它会瞬移到新位置。
      // 强制一次重排让动画能重新触发(re-trigger 的标准做法)。
      popup.classList.remove("is-in");
      void popup.offsetWidth;
      popup.classList.add("is-in");
    }

    // 把当前文章上下文写到 popup 上,供"加入生词本"按钮使用
    function setVocabContext(word, definition, translation) {
      const articleId = (document.querySelector("article.entry") || {}).id
                          ?.replace(/^article-/, "") || "";
      const issueKeyMatch = location.pathname.match(/(\d{4}-W\d{2})/);
      const issueKey = issueKeyMatch ? issueKeyMatch[1]
                                      : (document.body.dataset.issueKey || "");
      const sentenceEl = document.querySelector(".entry-body p");
      popup.dataset.articleId = articleId;
      popup.dataset.issueKey = issueKey;
      popup.dataset.word = word;
      popup.dataset.definition = definition || "";
      popup.dataset.translation = translation || "";
      popup.dataset.sentence = sentenceEl ? sentenceEl.textContent.trim().slice(0, 240) : "";
    }

    // ---- 确定性关闭模型:弹窗一旦显示,只有三种方式关闭 ----
    //   1. 点弹窗外的任意区域  2. 按 Esc  3. 点弹窗上的 ✕
    // 没有任何计时器/鼠标移出逻辑 — 迟到的词典响应、缓慢的鼠标移动都不影响它。
    function hide() {
      popup.hidden = true;
      active = null;
    }

    function sentenceContext(el) {
      // 取点击词所在句子的原文 — 提高 DeepSeek 释义准确度
      const p = el.closest("p");
      return p ? p.textContent.trim().slice(0, 240) : "";
    }

    // ---- 浏览器自带翻译(Chrome/Edge 内置 Translation API,零 token) ----
    const btCache = new Map();
    let btReady = null;   // 共享的 translator 实例(首次创建会下载语言包)

    function btGetTranslator() {
      if (!btReady) {
        btReady = (async () => {
          if (typeof Translator === "undefined") return null;
          const avail = await Translator.availability({ sourceLanguage: "en", targetLanguage: "zh" });
          if (avail === "unavailable") return null;
          return await Translator.create({ sourceLanguage: "en", targetLanguage: "zh" });
        })().catch(() => null);
      }
      return btReady;
    }

    let btStatus = "idle";   // idle | downloading | ready

    async function browserTranslate(word) {
      if (btCache.has(word)) return btCache.get(word);
      const p = (async () => {
        try {
          if (typeof Translator === "undefined") return { err: "unsupported" };
          const avail = await Translator.availability({ sourceLanguage: "en", targetLanguage: "zh" });
          if (avail === "unavailable") return { err: "unsupported" };
          if (avail !== "available") btStatus = "downloading";
          const t = await btGetTranslator();
          if (!t) return { err: "unsupported" };
          btStatus = "ready";
          const out = (await t.translate(word)) || "";
          return out.trim() ? { zh: out.trim() } : { err: "empty" };
        } catch (e) {
          // 语言包下载被阻止(需要用户手势)或下载失败
          return { err: "blocked" };
        }
      })();
      btCache.set(word, p);
      return p;
    }

    // 用户首次点击页面时后台预热翻译模型(下载语言包),之后悬停秒回
    document.addEventListener("click", () => { btGetTranslator(); }, { once: true });

    // ---- 翻译引擎状态提示(右下角轻量浮动条) ----
    function showXlatStatus() {
      const box = document.createElement("div");
      box.className = "xlat-status";
      box.hidden = true;
      document.body.appendChild(box);
      let timer = null;
      const show = (text, opts) => {
        box.textContent = text;
        box.hidden = false;
        box.classList.toggle("is-err", !!(opts && opts.err));
        box.classList.toggle("is-ok", !!(opts && opts.ok));
        // 状态条常驻 DOM、靠 hidden 反复开关,重放一次淡入免得每次都硬蹦出来
        box.classList.remove("is-in");
        void box.offsetWidth;
        box.classList.add("is-in");
        if (opts && opts.autoHide) {
          clearTimeout(timer);
          timer = setTimeout(() => { box.hidden = true; }, opts.autoHide);
        }
      };
      if (typeof Translator === "undefined") {
        show("当前浏览器不支持内置翻译 · 建议用 Chrome / Edge 打开", { err: true });
        return;
      }
      show("翻译引擎检查中…");
      let a;
      try {
        a = Translator.availability({ sourceLanguage: "en", targetLanguage: "zh" });
      } catch (_) {
        show("点击页面任意处以启用内置翻译", { err: true });
        return;
      }
      Promise.resolve(a).then(avail => {
        if (avail === "unavailable") {
          show("内置翻译语言包不可用", { err: true });
          return;
        }
        if (avail === "available") {
          show("翻译引擎就绪 · 悬停生词即查", { ok: true, autoHide: 2200 });
          return;
        }
        show("正在下载翻译引擎（首次使用，约 10–30 秒）…");
        const kick = () => btGetTranslator().then(t => {
          if (t) { btStatus = "ready"; show("翻译引擎就绪 · 悬停生词即查", { ok: true, autoHide: 2200 }); }
          else show("翻译引擎下载失败 · 请检查网络后刷新", { err: true });
        }).catch(() => show("点击页面任意处以启用内置翻译", { err: true }));
        kick();
        document.addEventListener("click", kick, { once: true });
      }).catch(() => show("点击页面任意处以启用内置翻译", { err: true }));
    }
    showXlatStatus();

    const OFFLINE_MSG = "AI service is not available offline.";

    async function lookup(word, ctx) {
      const key = word + "|" + (ctx || "").slice(0, 60);
      if (cache.has(key)) return cache.get(key);
      const p = (async () => {
        try {
          const url = `/api/dict?word=${encodeURIComponent(word)}` +
                      (ctx ? `&sentence=${encodeURIComponent(ctx)}` : "");
          const r = await fetch(url, { method: "GET" });
          if (r.ok) {
            const data = await r.json();
            if (data && (data.translation || data.definition_en)) return data;
          }
        } catch (_) { /* offline */ }

        return {
          word,
          phonetic: "",
          translation: "AI service is not available offline.",
          definition_en: "",
          examples: [],
          cefr_level: "",
        };
      })();
      // 失败的查询不进缓存 — 修好 key 后刷新即可重试,不会一直显示 offline
      p.then(info => {
        if (info && info.translation !== OFFLINE_MSG) cache.set(key, p);
      });
      return p;
    }

    function onEnter(e) {
      const el = e.target.closest(".rare, .vocab-word");
      if (!el || !article.contains(el)) return;
      const word = (el.dataset.word || el.textContent || "").trim().toLowerCase();
      if (!word) return;
      const ctx = sentenceContext(el);
      active = { el, word, ctx };
      // 立即显示加载态 — 体感秒开,释义返回后原地填充
      if (popup.hidden || popup.dataset.word !== word) {
        setVocabContext(word, "", "");
        showAt(el,
          `<div class="vp-word">${escapeHtml(word)}` +
          `<button class="vp-close" data-close-popup title="Close">✕</button></div>` +
          `<div class="vp-trans vp-loading">` +
          (btStatus === "downloading" ? "正在下载翻译语言包…" : "翻译中…") +
          `</div>`);
      }
      // 只用浏览器内置翻译 — 单词查词不调用任何 AI
      browserTranslate(word).then(res => {
        const info = res && res.zh
          ? { word, phonetic: "", definition_en: "",
              translation: res.zh + "（浏览器翻译）", examples: [], cefr_level: "" }
          : { word, phonetic: "", definition_en: "",
              translation: {
                unsupported: "此浏览器不支持内置翻译 — 请用 Chrome / Edge 打开（无需联网 AI）",
                blocked: "首次使用需下载翻译语言包：请先点击页面任意处，等 10-30 秒后再悬停",
                empty: "未获取到释义，请再悬停一次",
              }[res && res.err] || "翻译不可用",
              examples: [], cefr_level: "" };
        if (!active || active.word !== word) return;
        // 弹窗已展示同一词的完整内容时不要重建 DOM —
        // 否则迟到的响应会在用户点击按钮的瞬间替换按钮,点击落空
        if (!popup.hidden && popup.dataset.word === word
            && popup.querySelector(".vp-add") && popup.textContent.indexOf("Add to") !== -1) {
          setVocabContext(word, info.definition_en || "", info.translation || "");
          return;
        }
        const def = info.definition_en || "";
        const tr  = info.translation || "";
        setVocabContext(word, def, tr);
        const html =
          `<div class="vp-word">${escapeHtml(info.word)}` +
          (info.phonetic ? `<span class="vp-phon">${escapeHtml(info.phonetic)}</span>` : "") +
          (info.cefr_level ? `<span class="vp-level">${escapeHtml(info.cefr_level)}</span>` : "") +
          `<button class="vp-close" data-close-popup title="Close">✕</button>` +
          `</div>` +
          (def ? `<div class="vp-trans">${escapeHtml(def)}</div>` : "") +
          (tr && tr !== def ? `<div class="vp-trans">${escapeHtml(tr)}</div>` : "") +
          `<button class="vp-add" data-add-vocab>＋ Add to my words</button>`;
        showAt(el, html);
      });
    }

    article.addEventListener("mouseover", onEnter);
    article.addEventListener("click", e => {
      const el = e.target.closest(".rare, .vocab-word");
      if (el && article.contains(el)) onEnter({ target: el });
    });
    // 关闭通道:✕ 按钮 / 弹窗外点击 / Esc
    popup.addEventListener("click", e => {
      if (e.target.closest("[data-close-popup]")) hide();
    });
    document.addEventListener("click", e => {
      if (popup.hidden) return;
      if (popup.contains(e.target) || e.target.closest(".rare, .vocab-word")) return;
      hide();
    });
    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && !popup.hidden) hide();
    });

    function escapeHtml(s) {
      return String(s).replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;",
        '"': "&quot;", "'": "&#39;"
      }[c]));
    }
  }

  /* ---------------- AI 提问面板 ---------------- */
  function bindAskPanel() {
    const article = document.querySelector("article.entry");
    if (!article) return;

    // 仅在已有 .ask-mount 节点时挂载(由 article.html.j2 注入)
    const mount = document.querySelector(".ask-mount");
    if (!mount) return;

    mount.innerHTML = `
      <details class="ask-panel">
        <summary>Ask about this article · powered by AI</summary>
        <form class="ask-form" autocomplete="off">
          <input type="text" name="q" placeholder="e.g. What does \"flattened\" mean here?" />
          <button class="btn" type="submit">Ask</button>
        </form>
        <div class="ask-out" hidden></div>
      </details>`;

    const form = mount.querySelector(".ask-form");
    const out = mount.querySelector(".ask-out");

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const q = form.q.value.trim();
      if (!q) return;
      out.hidden = false;
      out.textContent = "Thinking…";
      try {
        const r = await fetch("/api/ask", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            question: q,
            issue_key: (location.pathname.match(/(\d{4}-W\d{2})/) || [])[1]
                       || document.body.dataset.issueKey || "",
          }),
        });
        const data = await r.json();
        // 聊开后:面板保持展开,输出区平滑滚入视野
        const panel = mount.querySelector(".ask-panel");
        if (panel) panel.open = true;
        out.textContent = data.content || "(no answer)";
        out.scrollIntoView({ behavior: "smooth", block: "nearest" });

        const used = data.usage_tokens || 0;
        let total = parseInt(localStorage.getItem("we_ask_tokens") || "0", 10) + used;
        localStorage.setItem("we_ask_tokens", String(total));
        const usageLine = document.createElement("div");
        usageLine.className = "ask-usage";
        usageLine.textContent = `≈ ${total} tokens used so far`;
        out.appendChild(usageLine);
        if (total >= 6000 && !localStorage.getItem("we_ask_nudged")) {
          localStorage.setItem("we_ask_nudged", "1");
          const tip = document.createElement("div");
          tip.className = "ask-usage";
          tip.textContent = "小提示:AI 对话按 token 计量,目前累计约 " + total + " tokens,注意用量哦。";
          out.appendChild(tip);
        }
      } catch (err) {
        out.textContent = "AI service is unreachable.";
      }
    });
  }

  /* ---------------- 账户入口感知 ---------------- */
  function bindAccountLink() {
    const link = document.getElementById("account-link");
    if (!link) return;
    const token = localStorage.getItem("we_token");
    if (!token) {
      link.textContent = "Sign in";
      link.href = "/auth/login";
      return;
    }
    // 验证 token 还可用
    fetch("/auth/me", { headers: { "Authorization": "Bearer " + token } })
      .then(r => r.ok ? r.json() : null)
      .then(u => {
        if (!u) {
          localStorage.removeItem("we_token");
          link.textContent = "Sign in";
          link.href = "/auth/login";
        } else {
          link.textContent = u.email;
          link.href = "/me";
        }
      })
      .catch(() => {
        link.textContent = "Sign in";
        link.href = "/auth/login";
      });
  }

  /* ---------------- 阅读进度上报 ---------------- */
  function bindReadingProgress() {
    const article = document.querySelector("article.entry");
    if (!article) return;
    const token = localStorage.getItem("we_token");
    if (!token) return;  // 未登录不上报

    const articleId = article.id.replace(/^article-/, "");
    const issueKeyMatch = location.pathname.match(/(\d{4}-W\d{2})/);
    const issueKey = issueKeyMatch ? issueKeyMatch[1] : (document.body.dataset.issueKey || "");

    let seconds = 0;
    const start = Date.now();
    const tick = setInterval(() => {
      seconds = Math.round((Date.now() - start) / 1000);
    }, 1000);

    let lastReport = 0;
    function report(completed) {
      const scrollPct = computeScrollPct();
      fetch("/api/v1/progress", {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + token,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          issue_key: issueKey,
          article_id: articleId,
          seconds: seconds,
          scroll_pct: scrollPct,
          completed: !!completed,
        }),
        keepalive: true,
      }).catch(() => {});

      // 顺手记一次历史
      fetch("/api/history", {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + token,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          issue_key: issueKey,
          article_id: articleId,
          title: (article.querySelector("h1") || {}).textContent || "",
        }),
        keepalive: true,
      }).catch(() => {});
    }

    function computeScrollPct() {
      const body = article.querySelector(".entry-body") || article;
      const total = body.scrollHeight - window.innerHeight;
      if (total <= 0) return 100;
      const pct = (window.scrollY - body.offsetTop) / total * 100;
      return Math.max(0, Math.min(100, pct));
    }

    let raf;
    window.addEventListener("scroll", () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const now = Date.now();
        if (now - lastReport > 10000) {
          lastReport = now;
          report(false);
        }
      });
    }, { passive: true });

    // 离开或关闭页面前再报一次
    window.addEventListener("pagehide", () => {
      clearInterval(tick);
      report(computeScrollPct() >= 95);
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") {
        report(computeScrollPct() >= 95);
      }
    });
  }

  /* ---------------- 生词本加入按钮 ---------------- */
  function bindVocabAddButton() {
    const popup = document.querySelector(".vocab-popup");
    if (!popup) return;

    popup.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-add-vocab]");
      if (!btn) return;
      const token = localStorage.getItem("we_token");
      if (!token) {
        // confirm() 在部分浏览器/WebView 会被静默拦截 — 改为按钮自身变成登录入口
        btn.removeAttribute("data-add-vocab");
        btn.textContent = "Sign in to save →";
        btn.addEventListener("click", () => { location.href = "/auth/login"; });
        return;
      }
      const word = popup.dataset.word;   // data-word 在弹窗容器上,不在按钮上
      const articleId = popup.dataset.articleId;
      const issueKey = popup.dataset.issueKey;
      const definition = popup.dataset.definition || "";
      // 浏览器翻译的显示标注不入库
      const translation = (popup.dataset.translation || "").replace("（浏览器翻译）", "");
      const sentence = popup.dataset.sentence || "";
      btn.disabled = true;
      btn.textContent = "Saving…";
      try {
        const r = await fetch("/api/v1/vocab", {
          method: "POST",
          headers: {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            word, definition, translation, sentence,
            issue_key: issueKey, article_id: articleId,
          }),
        });
        const data = await r.json();
        if (r.ok) {
          btn.textContent = data.updated ? "Updated" : "Added ✓";
          btn.classList.add("is-added");
        } else if (r.status === 401) {
          // 登录过期 — 清掉旧 token,按钮变成重新登录入口
          localStorage.removeItem("we_token");
          btn.removeAttribute("data-add-vocab");
          btn.disabled = false;
          btn.textContent = "Sign in again →";
          btn.addEventListener("click", () => { location.href = "/auth/login"; });
        } else {
          btn.textContent = "Failed (" + r.status + ")";
          btn.disabled = false;
        }
      } catch (err) {
        btn.textContent = "Failed";
        btn.disabled = false;
      }
    });
  }

  /* ---------------- 精读页:Web Speech 朗读 ---------------- */
  let currentUtter = null;
  let currentBtn = null;

  function stopCurrent() {
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    if (currentBtn) { currentBtn.classList.remove("playing"); currentBtn = null; }
    currentUtter = null;
  }

  function speak(target, btn, lang) {
    stopCurrent();
    if (!window.speechSynthesis) {
      btn.classList.add("playing");
      setTimeout(() => btn.classList.remove("playing"), 800);
      return;
    }
    const text = Array.from(target.querySelectorAll("p"))
      .map(p => p.textContent.trim())
      .filter(Boolean).join(". ");
    if (!text.trim()) return;

    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang;
    u.rate = 0.96;
    u.pitch = 1.0;
    u.onend = () => { btn.classList.remove("playing"); currentBtn = null; currentUtter = null; };
    u.onerror = () => { btn.classList.remove("playing"); currentBtn = null; currentUtter = null; };
    btn.classList.add("playing");
    currentBtn = btn; currentUtter = u;
    window.speechSynthesis.speak(u);
  }

  function bindReading() {
    document.querySelectorAll(".play-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const tid = btn.dataset.target;
        const lang = btn.dataset.lang || "en";
        const target = tid && document.getElementById(tid);
        if (!target) return;
        speak(target, btn, lang === "zh" ? "zh-CN" : "en-US");
      });
      btn.addEventListener("dblclick", stopCurrent);
    });
    bindUploads();
  }

  /* ---------------- 上传自定义音频 ---------------- */
  const userAudios = {};

  function bindUploads() {
    document.querySelectorAll('.upload-btn input[type="file"]').forEach(input => {
      input.addEventListener("change", e => {
        const file = e.target.files && e.target.files[0];
        if (!file) return;
        const label = input.closest(".upload-btn");
        const articleId = label.dataset.article || "";
        const url = URL.createObjectURL(file);
        userAudios[articleId] = url;
        renderAudioSlot(articleId, url, file.name);
      });
    });
  }

  function renderAudioSlot(articleId, url, fileName) {
    const article = document.getElementById("article-" + articleId);
    if (!article) return;
    let slot = article.querySelector(".user-audio");
    if (!slot) {
      slot = document.createElement("div");
      slot.className = "user-audio";
      slot.style.cssText =
        "margin:14px 0 6px;padding:10px 14px;background:#fff8eb;" +
        "border:1px dashed #d4b478;border-radius:6px;display:flex;" +
        "gap:12px;align-items:center;flex-wrap:wrap;";
      const tools = article.querySelector(".entry-tools");
      tools && tools.insertAdjacentElement("afterend", slot);
    }
    slot.innerHTML =
      `<span style="font-size:13px;color:#8c6d46;font-family:'Source Serif Pro',Georgia,serif;">` +
      `Your recording · ${fileName}</span>` +
      `<audio controls src="${url}" style="height:36px;flex:1;min-width:240px;"></audio>` +
      `<a class="btn" href="${url}" download="${fileName}">Download</a>` +
      `<button class="btn" data-action="clear-uploaded">Remove</button>`;
    slot.querySelector('[data-action="clear-uploaded"]')
      ?.addEventListener("click", () => {
        URL.revokeObjectURL(userAudios[articleId]);
        delete userAudios[articleId];
        slot.remove();
      });
  }

  /* ---------------- init ---------------- */
  document.addEventListener("DOMContentLoaded", () => {
    window.__initErrors = [];
    const safe = (name, fn) => {
      try { fn(); }
      catch (e) { window.__initErrors.push(name + ": " + (e && e.message || e)); }
    };
    safe("bindNav", bindNav);
    if (page === "index") safe("bindIndex", bindIndex);
    if (page === "article") {
      safe("bindReading", bindReading);
      safe("highlightRare", highlightRare);
      safe("bindVocabPopup", bindVocabPopup);
      safe("bindVocabAddButton", bindVocabAddButton);
      safe("bindAskPanel", bindAskPanel);
      safe("bindReadingProgress", bindReadingProgress);
    }
    if (page !== "auth") safe("bindAccountLink", bindAccountLink);
  });
})();