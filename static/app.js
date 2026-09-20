/* ------------------------------------------------------------------
   Weekly English — 前端交互
   - 主页:搜索(标题/正文) + 难度 + 来源筛选
   - 精读页:生词高亮、Web Speech 朗读、上传自定义音频
   - 全部用 SVG 图标,不使用 emoji
   ------------------------------------------------------------------ */

(() => {
  "use strict";

  /* ---------------- 主页筛选 ---------------- */
  const page = document.body.dataset.page;

  function bindIndex() {
    const cards = Array.from(document.querySelectorAll(".card"));
    const status = document.getElementById("status");
    const empty = document.getElementById("empty");
    if (!cards.length) return;

    const search = document.getElementById("q");
    let level = "all";
    let source = "all";

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
      document.querySelectorAll(".level-block").forEach(block => {
        if (filtering) block.classList.remove("is-closed");
        const lvl = block.dataset.block || "";
        block.hidden = filtering && !!lvl && !blockVisible[lvl];
      });
      if (status) status.textContent = `Showing ${n} of ${cards.length}`;
      if (empty) empty.hidden = n !== 0;
    }

    // 层标题点击 = 折叠/展开该层
    document.querySelectorAll("[data-toggle-block]").forEach(head => {
      head.addEventListener("click", () => {
        const block = head.closest(".level-block");
        if (block) block.classList.toggle("is-closed");
      });
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
    }

    // 把当前文章上下文写到 popup 上,供"加入生词本"按钮使用
    function setVocabContext(word, definition, translation) {
      const articleId = (document.querySelector("article.entry") || {}).id
                          ?.replace(/^article-/, "") || "";
      const issueKeyMatch = location.pathname.match(/article\/([\d]{4}-W\d{2})\//);
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

    let hideTimer = null;
    let pinned = false;   // 点击单词后钉住弹窗,点弹窗/单词以外才关

    // 延迟关闭;到点时实时复查 — 鼠标仍停在单词或弹窗上就无限续期
    function hide() {
      if (pinned) return;
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = setTimeout(() => {
        hideTimer = null;
        try {
          if ((active && active.el && active.el.matches(":hover"))
              || popup.matches(":hover")) {
            hide();   // 还悬停着 → 续期,不关
            return;
          }
        } catch (_) { /* 老浏览器不支持 :hover 匹配,按原逻辑关 */ }
        popup.hidden = true;
        active = null;
      }, 400);
    }

    popup.addEventListener("mouseenter", () => {
      if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    });
    popup.addEventListener("mouseleave", () => {
      if (pinned) return;
      popup.hidden = true;
      active = null;
    });

    function sentenceContext(el) {
      // 取点击词所在句子的原文 — 提高 DeepSeek 释义准确度
      const p = el.closest("p");
      return p ? p.textContent.trim().slice(0, 240) : "";
    }

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
      lookup(word, ctx).then(info => {
        if (!active || active.word !== word) return;
        const def = info.definition_en || "";
        const tr  = info.translation || "";
        setVocabContext(word, def, tr);
        const html =
          `<div class="vp-word">${escapeHtml(info.word)}` +
          (info.phonetic ? `<span class="vp-phon">${escapeHtml(info.phonetic)}</span>` : "") +
          (info.cefr_level ? `<span class="vp-level">${escapeHtml(info.cefr_level)}</span>` : "") +
          `</div>` +
          (def ? `<div class="vp-trans">${escapeHtml(def)}</div>` : "") +
          (tr && tr !== def ? `<div class="vp-trans">${escapeHtml(tr)}</div>` : "") +
          `<button class="vp-add" data-add-vocab>＋ Add to my words</button>`;
        showAt(el, html);
      });
    }
    function onLeave(e) {
      const el = e.target.closest(".rare, .vocab-word");
      if (el && article.contains(el)) hide();
    }

    function escapeHtml(s) {
      return String(s).replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;",
        '"': "&quot;", "'": "&#39;"
      }[c]));
    }

    article.addEventListener("mouseover", onEnter);
    article.addEventListener("mouseout", onLeave);
    article.addEventListener("click", e => {
      const el = e.target.closest(".rare, .vocab-word");
      if (el && article.contains(el)) {
        onEnter({ target: el });
        pinned = true;   // 点击 = 钉住,弹窗不再跟随鼠标离开消失
      }
    });
    // 点击弹窗和单词以外的区域 → 取消钉住并关闭
    document.addEventListener("click", e => {
      if (!pinned) return;
      if (popup.contains(e.target) || e.target.closest(".rare, .vocab-word")) return;
      pinned = false;
      popup.hidden = true;
      active = null;
    });
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
    const issueKeyMatch = location.pathname.match(/article\/([\d]{4}-W\d{2})\//);
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
      const word = btn.dataset.word;
      const articleId = popup.dataset.articleId;
      const issueKey = popup.dataset.issueKey;
      const definition = popup.dataset.definition || "";
      const translation = popup.dataset.translation || "";
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
    if (page === "index") bindIndex();
    if (page === "article") {
      bindReading();
      highlightRare();
      bindVocabPopup();
      bindVocabAddButton();
      bindAskPanel();
      bindReadingProgress();
    }
    if (page !== "auth") bindAccountLink();
  });
})();