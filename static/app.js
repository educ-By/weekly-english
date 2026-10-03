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

    // 动画参数从 CSS 令牌里读一次,不另写一份时长/缓动
    const rootStyle = getComputedStyle(document.documentElement);
    const durMs = (name, fallback) =>
      (parseFloat(rootStyle.getPropertyValue(name)) || fallback) * 1000;
    const POP_EASE = (rootStyle.getPropertyValue("--ease") || "").trim()
                     || "cubic-bezier(0.22,0.61,0.36,1)";

    // 弹窗与状态条常驻同一个 DOM、只改内容,所以每次显示都要重放一遍淡入。
    // 用 WAAPI 而不是"摘类名 → 读 offsetWidth → 加类名":后者得靠一次强制重排
    // 才能让动画重播,每次悬停/每次提示都白搭一次布局。
    function replayPopIn(el, holder, duration) {
      if (prefersReducedMotion()) return;
      if (!el.animate) {                    // 老浏览器退回类名重放
        el.classList.remove("is-in");
        void el.offsetWidth;
        el.classList.add("is-in");
        return;
      }
      if (holder.anim) holder.anim.cancel();
      holder.anim = el.animate(
        [{ opacity: 0, transform: "translateY(3px) scale(0.985)" },
         { opacity: 1, transform: "none" }],
        { duration, easing: POP_EASE });
    }

    const popupAnim = { anim: null };
    const popIn = () => replayPopIn(popup, popupAnim, durMs("--dur-fast", 0.13));

    function showAt(target, text) {
      const r = (target && typeof target.getBoundingClientRect === "function")
        ? target.getBoundingClientRect() : target;
      popup.innerHTML = text;
      popup.hidden = false;
      // 唯一一次强制布局:量尺寸用来居中和按视口夹取。写完内容必须量一次,
      // 省不掉;原来这里后面还有一次,是为了重放动画,已经交给 WAAPI 了。
      const pw = popup.offsetWidth;
      const ph = popup.offsetHeight;
      const sx = window.scrollX || 0;
      const sy = window.scrollY || 0;

      let x = r.left + r.width / 2 - pw / 2;
      // 水平按视口夹住,再折算成文档坐标 —— 弹窗是 absolute,坐标要含滚动偏移
      x = Math.max(8, Math.min(window.innerWidth - pw - 8, x)) + sx;

      let yView = r.top - ph - 4;   // 紧贴单词,不留缝隙,鼠标移过去不会穿过空档
      if (yView < 8) yView = r.bottom + 4;
      const y = Math.max(8, yView + sy);

      popup.style.left = x + "px";
      popup.style.top = y + "px";
      popIn();
    }

    // 把当前文章上下文写到 popup 上,供"加入生词本"按钮使用
    function setVocabContext(word, definition, translation, sentence) {
      const articleId = (document.querySelector("article.entry") || {}).id
                          ?.replace(/^article-/, "") || "";
      const issueKeyMatch = location.pathname.match(/(\d{4}-W\d{2})/);
      const issueKey = issueKeyMatch ? issueKeyMatch[1]
                                      : (document.body.dataset.issueKey || "");
      const firstP = document.querySelector(".entry-body p");
      popup.dataset.articleId = articleId;
      popup.dataset.issueKey = issueKey;
      popup.dataset.word = word;
      popup.dataset.definition = definition || "";
      popup.dataset.translation = translation || "";
      // 优先用调用方给的句子/当前词的所属句 —— 不再无脑取第一段
      const sent = sentence != null ? sentence
        : (active && active.ctx) || (firstP ? firstP.textContent.trim().slice(0, 240) : "");
      popup.dataset.sentence = sent || "";
    }

    // 弹窗正文统一走这里:写回 dataset(供"加入生词本"用)+ 渲染
    // extra 追加在按钮之后(引擎不可用时的"在线词典"退路);force 用于用户主动查询后覆盖旧内容
    function drawCard(el, info, extra, force) {
      const def = info.definition_en || "";
      const tr  = info.translation || "";
      // 弹窗已展示同一词的完整内容时不要重建 DOM —
      // 否则迟到的响应会在用户点击按钮的瞬间替换按钮,点击落空。
      // 例外:当前显示的是"引擎不可用"(带在线词典按钮)时允许覆盖,否则引擎恢复后还停在错误文案上。
      if (!force && !popup.hidden && popup.dataset.word === info.word
          && popup.querySelector(".vp-add") && !popup.querySelector("[data-online-lookup]")
          && popup.textContent.indexOf("Add to") !== -1) {
        setVocabContext(info.word, def, tr);
        return;
      }
      setVocabContext(info.word, def, tr);
      const html =
        `<div class="vp-word">${escapeHtml(info.word)}` +
        (info.phonetic ? `<span class="vp-phon">${escapeHtml(info.phonetic)}</span>` : "") +
        (info.cefr_level ? `<span class="vp-level">${escapeHtml(info.cefr_level)}</span>` : "") +
        `<button class="vp-close" data-close-popup title="Close">✕</button>` +
        `</div>` +
        (def ? `<div class="vp-trans">${escapeHtml(def)}</div>` : "") +
        (tr && tr !== def ? `<div class="vp-trans">${escapeHtml(tr)}</div>` : "") +
        `<button class="vp-add" data-add-vocab>＋ Add to my words</button>` +
        (extra || "");
      showAt(el, html);
    }

    // 内置引擎用不了时的退路:/api/dict(服务端共享词典,命中过就直接复用,不重复烧 token)
    async function onlineLookup(btn) {
      const word = popup.dataset.word;
      if (!active || active.word !== word) return;
      const ctx = active.ctx || popup.dataset.sentence || "";
      btn.disabled = true;
      btn.textContent = "查询中…";
      try {
        const url = `/api/dict?word=${encodeURIComponent(word)}` +
                    (ctx ? `&sentence=${encodeURIComponent(ctx)}` : "");
        const r = await fetch(url);
        const info = await r.json();
        const zh = (info && (info.translation || info.definition_en)) || "";
        if (!zh) throw new Error("empty");
        drawCard(active.anchor || active.el, {
          word,
          phonetic: info.phonetic || "",
          definition_en: info.definition_en || "",
          translation: info.translation || "",
          cefr_level: info.cefr_level || "",
        }, "", true);
      } catch (_) {
        btn.disabled = false;
        btn.textContent = "查询失败，稍后再试";
      }
    }

    // ---- 确定性关闭模型:弹窗一旦显示,只有这几种方式关闭 ----
    //   1. 点弹窗外的任意区域  2. 按 Esc  3. 点弹窗上的 ✕  4. 滚动页面
    // 全部是用户主动手势 —— 没有任何计时器/鼠标移出逻辑,
    // 迟到的词典响应、缓慢的鼠标移动都不影响它。
    function hide() {
      popup.hidden = true;
      active = null;
      clearSel();
      activeSel = null;
    }

    // 滚动即关闭:滚动时弹窗会从那个词旁边漂开(它只定位一次,不逐帧跟随),
    // 与其让它飘着,不如跟着用户的滚动收起。用捕获阶段监听,
    // 这样任何可滚动容器里的滚动也都能收到。
    document.addEventListener("scroll", () => {
      if (!popup.hidden) hide();
    }, { passive: true, capture: true });

    function sentenceContext(el) {
      // 取点击词所在句子的原文 — 提高 DeepSeek 释义准确度
      const p = el.closest("p");
      return p ? p.textContent.trim().slice(0, 240) : "";
    }

    // ---- 浏览器自带翻译(Chrome/Edge 内置 Translation API,零 token) ----
    // 两个坑必须守住,bug 全出在这两处:
    //   ① 语言包尚未下载时,Translator.create() 要求用户手势,否则抛 NotAllowedError。
    //      所以页面加载时自动预热是注定失败的 —— 只能等用户点过页面再去建。
    //   ② 失败绝不可缓存。旧版把这次注定失败的预热钉成 null,于是浏览器明明支持 API,
    //      用户之后怎么点都不再重试,一律收到"此浏览器不支持内置翻译"这句错话。
    const btCache = new Map();   // word → Promise<{zh} | {err}>
    let btTranslator = null;     // 建好的 translator 实例
    let btPending = null;        // 进行中的创建(单飞;一结束就清空,留出重试)
    let btPhase = "idle";        // idle | no-api | unavailable | no-response | need-gesture | downloading | ready | failed
    let btVerdict = "";          // 会话级结论(no-api/unavailable/no-response):悬停时直接复用,不必每次等探测器超时
    let btPct = 0;
    let btNote = "";             // 最近一次失败原因原文(排障用)

    const BT_PAIR = { sourceLanguage: "en", targetLanguage: "zh" };
    const btHasAPI = () => typeof Translator !== "undefined";
    // 用户手势:Chromium 里"点过页面"长期有效(navigator.userActivation 老浏览器上不存在)
    const btHasGesture = () => {
      const ua = navigator.userActivation;
      return !!(ua && (ua.isActive || ua.hasBeenActive));
    };

    // availability() 在个别实现上会一直不返回 —— 不能把弹窗就这么挂在"翻译中…"上
    function btAvailability() {
      return Promise.race([
        Promise.resolve(Translator.availability(BT_PAIR)),
        new Promise((_, rej) => setTimeout(() => rej(new Error("availability-timeout")), 8000)),
      ]);
    }

    function btCreate() {
      const attempt = (withMonitor) => {
        const opts = { ...BT_PAIR };
        if (withMonitor) {
          opts.monitor = m => {
            try {
              m.addEventListener("downloadprogress", ev => {
                btPct = Math.round((ev.loaded || 0) * 100);
                btRefresh();
              });
            } catch (_) { /* 不支持进度事件不影响翻译,只是没有百分比 */ }
          };
        }
        return Translator.create(opts);
      };
      return new Promise((resolve, reject) => {
        // 语言包可能要下几十秒,但卡死的 create 不能永久占着弹窗
        const timer = setTimeout(() => reject(new Error("download-timeout")), 180000);
        const settle = fn => v => { clearTimeout(timer); fn(v); };
        let p;
        try {
          p = attempt(true).catch(e => {
            // 个别实现不认 monitor 选项 —— 退回最朴素的调用,别让进度条拖垮翻译本身
            if (e instanceof TypeError) return attempt(false);
            throw e;
          });
        } catch (e) { clearTimeout(timer); reject(e); return; }
        p.then(settle(resolve), settle(reject));
      });
    }

    // force=true 只用在"用户刚点过页面"这种场合:值得把结论作废重探一次
    function btEnsure(force) {
      if (btTranslator) return Promise.resolve(btTranslator);
      if (btPending) return btPending;
      if (btVerdict && !force) { btPhase = btVerdict; return Promise.resolve(null); }
      const run = (async () => {
        if (!btHasAPI()) { btPhase = btVerdict = "no-api"; return null; }
        let avail;
        try {
          avail = await btAvailability();
        } catch (e) {
          if (e && e.message === "availability-timeout") {
            btPhase = btVerdict = "no-response";
          } else {
            btPhase = btVerdict = "unavailable";
          }
          btNote = String(e); return null;
        }
        if (avail === "unavailable") { btPhase = btVerdict = "unavailable"; return null; }
        // 缺语言包又没手势:这不是"不支持",只是还差一次点击 —— 不试、也不记失败
        if (avail !== "available" && !btHasGesture()) { btPhase = "need-gesture"; return null; }
        if (avail !== "available") { btPhase = "downloading"; btPct = 0; btRefresh(true); }
        try {
          btTranslator = await btCreate();
          btPhase = "ready"; btVerdict = ""; btPct = 100; btNote = "";
        } catch (e) {
          btPhase = (e && e.name === "NotAllowedError") ? "need-gesture" : "failed";
          btNote = ((e && e.name) ? e.name + ": " : "") + ((e && e.message) || e);
        }
        return btTranslator;
      })();
      btPending = run.finally(() => { btPending = null; });
      return btPending;
    }

    async function browserTranslate(word) {
      if (btCache.has(word)) return btCache.get(word);
      const p = (async () => {
        const t = await btEnsure();
        if (!t) return { err: btPhase };
        try {
          const out = ((await t.translate(word)) || "").trim();
          return out ? { zh: out } : { err: "empty" };
        } catch (e) {
          // 实例失效(引擎被回收 / 语言包被清理)—— 丢掉它,下次重建
          btTranslator = null; btPhase = "idle"; return { err: "failed" };
        }
      })();
      btCache.set(word, p);
      p.then(res => { if (!res || !res.zh) btCache.delete(word); });   // 失败不入缓存,可重试
      return p;
    }

    // ---- 翻译引擎状态提示(右下角轻量浮动条) ----
    let toastBox = null, toastTimer = null, toastKind = "";
    const toastAnim = { anim: null };

    function toast(text, kind, autoHide) {
      if (!toastBox) {
        toastBox = document.createElement("div");
        toastBox.className = "xlat-status";
        toastBox.hidden = true;
        document.body.appendChild(toastBox);
      }
      toastKind = kind || "";
      toastBox.textContent = text;
      toastBox.hidden = false;
      toastBox.classList.toggle("is-err", kind === "err");
      toastBox.classList.toggle("is-ok", kind === "ok");
      // 状态条常驻 DOM、靠 hidden 反复开关,重放一次淡入免得每次都硬蹦出来
      replayPopIn(toastBox, toastAnim, durMs("--dur", 0.18));
      clearTimeout(toastTimer);
      if (autoHide) toastTimer = setTimeout(() => { toastBox.hidden = true; }, autoHide);
    }

    // 下载进度推送:状态条与弹窗里的"下载中"文案同步刷新(force 用于刚转入下载态时立刻改写状态条)
    function btRefresh(force) {
      if (btPhase !== "downloading") return;
      const text = "正在下载翻译语言包" + (btPct ? " " + btPct + "%" : "") + "…";
      if (force || toastKind === "download") toast(text, "download");
      const loading = popup.querySelector(".vp-loading");
      if (loading) loading.textContent = text;
    }

    function btReport() {
      switch (btPhase) {
        case "no-api":
          toast("这个浏览器没有内置翻译引擎 · 请用 Chrome / Edge 138+ 桌面版打开", "err"); break;
        case "unavailable":
          toast("浏览器没有开放内置翻译 · 可能被设置或策略关掉了", "err"); break;
        case "no-response":
          toast("浏览器内置翻译没有响应 · 点生词可用在线词典", "err"); break;
        case "need-gesture":
          toast("内置翻译语言包还没下载 · 点击页面任意处开始下载（约 10-60 秒）", "err"); break;
        case "downloading":
          btRefresh(true); break;
        case "ready":
          toast("翻译引擎就绪 · 悬停生词即查", "ok", 2200); break;
        case "failed":
          toast("翻译语言包下载失败 · 请检查网络后刷新重试", "err"); break;
        default:
          toast("翻译引擎检查中…");
      }
    }

    // 页面加载只探测(不建实例,免得必然失败);用户点过页面后才真正去下载
    btReport();
    btEnsure().then(btReport);
    document.addEventListener("click", () => { btEnsure(true).then(btReport); }, { once: true });

    // 排障入口:控制台跑 __bt() 可看到引擎当前状态与失败原文
    window.__bt = () => ({ phase: btPhase, pct: btPct, note: btNote,
                           hasAPI: btHasAPI(), gesture: btHasGesture() });

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
      active = { el, anchor: el, word, ctx };
      // 立即显示加载态 — 体感秒开,释义返回后原地填充
      if (popup.hidden || popup.dataset.word !== word) {
        setVocabContext(word, "", "", ctx);
        showAt(el,
          `<div class="vp-word">${escapeHtml(word)}` +
          `<button class="vp-close" data-close-popup title="Close">✕</button></div>` +
          `<div class="vp-trans vp-loading">` +
          (btPhase === "downloading" ? "正在下载翻译语言包…" : "翻译中…") +
          `</div>`);
      }
      // 默认只走浏览器内置翻译 — 悬停查词不调用任何 AI
      browserTranslate(word).then(res => {
        if (!active || active.word !== word) return;
        if (res && res.zh) {
          drawCard(el, { word, phonetic: "", definition_en: "",
                         translation: res.zh,
                         examples: [], cefr_level: "" });
          return;
        }
        const reason = (res && res.err) || "failed";
        const tip = {
          "no-api": "这个浏览器没有内置翻译引擎（需 Chrome / Edge 138+ 桌面版）",
          "unavailable": "浏览器没有开放内置翻译，可能被设置或策略关掉了",
          "no-response": "浏览器内置翻译没有响应（引擎未就绪）",
          "need-gesture": "语言包还没下载：点一下页面任意处开始下载，约 10-60 秒",
          "downloading": "正在下载语言包，下好后再悬停一次即可",
          "failed": "翻译语言包下载失败，请检查网络",
          "empty": "未获取到释义，请再悬停一次",
        }[reason] || "翻译不可用";
        // 内置引擎用不了时给一条退路,但必须由用户点 —— 不默默烧 AI 额度
        const fallback = reason === "empty"
          ? ""
          : `<button class="vp-add" data-online-lookup>用在线词典查这个词</button>`;
        drawCard(el, { word, phonetic: "", definition_en: "",
                       translation: tip, examples: [], cefr_level: "" }, fallback);
      });
    }

    article.addEventListener("mouseover", onEnter);
    article.addEventListener("click", e => {
      // 自绘划词有选区时不抢 .rare 的点击,避免和选区工具条打架
      if (activeSel && activeSel.toString().trim()) return;
      const el = e.target.closest(".rare, .vocab-word");
      if (el && article.contains(el)) onEnter({ target: el });
    });
    // 关闭通道:✕ 按钮 / 弹窗外点击 / Esc
    popup.addEventListener("click", e => {
      if (e.target.closest("[data-close-popup]")) { hide(); return; }
      const btn = e.target.closest("[data-online-lookup]");
      if (btn) { onlineLookup(btn); return; }
      const sbtn = e.target.closest("[data-selection-lookup]");
      if (sbtn) { selectionLookup(sbtn); return; }
      const cbtn = e.target.closest("[data-selection-copy]");
      if (cbtn) copySelection(cbtn);
    });
    document.addEventListener("click", e => {
      if (popup.hidden) return;
      if (popup.contains(e.target) || e.target.closest(".rare, .vocab-word")) return;
      // 自绘划词有选区时,紧接的 click 不要关掉刚弹出的工具条
      if (activeSel && activeSel.toString().trim()) return;
      hide();
    });
    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && !popup.hidden) hide();
    });

    // ---- 自绘划词:正文已关掉原生选择(user-select:none),选区与系统菜单都由我们接管 ----
    // 用指针事件自己算 Range + 画高亮层,浏览器的选区菜单/长按菜单根本不会出现。
    const selLayer = document.createElement("div");
    selLayer.className = "sel-layer";
    document.body.appendChild(selLayer);
    let activeSel = null;      // 当前自绘选区(供复制/取词)
    let selAnchor = null;      // 起点 caret
    let selecting = false;
    let longPress = null;
    const bodyEl = article.querySelector(".entry-body");

    // 选区矩形节点池 —— 拖选时每帧复用同一批 div,只改 transform 和尺寸。
    // 原先每帧先 selLayer.innerHTML="" 再重建,一秒能造/扔上百个节点。
    const rectPool = [];
    let rectCount = 0;

    function rectAt(i) {
      let d = rectPool[i];
      if (!d) {
        d = document.createElement("div");
        d.className = "sel-rect";
        d.style.display = "none";
        selLayer.appendChild(d);
        rectPool[i] = d;
      }
      return d;
    }

    function setRectCount(n) {
      for (let i = n; i < rectCount; i++) rectPool[i].style.display = "none";
      rectCount = n;
    }

    const clearSel = () => setRectCount(0);

    function caretRangeAt(x, y) {
      if (document.caretRangeFromPoint) return document.caretRangeFromPoint(x, y);
      if (document.caretPositionFromPoint) {           // Firefox
        const p = document.caretPositionFromPoint(x, y);
        if (!p) return null;
        const r = document.createRange();
        r.setStart(p.offsetNode, p.offset);
        r.collapse(true);
        return r;
      }
      return null;
    }
    function inBody(node) {
      if (!bodyEl || !node) return false;
      const el = node.nodeType === 1 ? node : node.parentElement;
      return !!(el && bodyEl.contains(el));
    }
    function paintRange(range) {
      const sx = window.scrollX || 0, sy = window.scrollY || 0;
      // 先把几何一次性读完,再统一写样式 —— 读写分离,不让浏览器夹在中间反复重排
      const boxes = [];
      for (const r of range.getClientRects()) {
        if (r.width < 1 || r.height < 1) continue;
        boxes.push(r);
      }
      for (let i = 0; i < boxes.length; i++) {
        const r = boxes[i];
        const d = rectAt(i);
        d.style.transform =
          `translate(${Math.round(r.left + sx)}px, ${Math.round(r.top + sy)}px)`;
        d.style.width = r.width + "px";
        d.style.height = r.height + "px";
        d.style.display = "";
      }
      setRectCount(boxes.length);
    }
    function beginSelect(x, y) {
      const r = caretRangeAt(x, y);
      if (!r || !inBody(r.startContainer)) return false;
      selAnchor = { node: r.startContainer, offset: r.startOffset };
      selecting = true;
      activeSel = null;
      document.body.classList.add("is-selecting");
      return true;
    }
    function updateSelect(x, y) {
      if (!selecting || !selAnchor) return;
      const cur = caretRangeAt(x, y);
      if (!cur || !inBody(cur.startContainer)) return;
      const a = document.createRange();
      a.setStart(selAnchor.node, selAnchor.offset);
      a.collapse(true);
      const range = document.createRange();
      try {
        if (a.compareBoundaryPoints(Range.START_TO_START, cur) <= 0) {
          range.setStart(selAnchor.node, selAnchor.offset);
          range.setEnd(cur.startContainer, cur.startOffset);
        } else {
          range.setStart(cur.startContainer, cur.startOffset);
          range.setEnd(selAnchor.node, selAnchor.offset);
        }
      } catch (_) { return; }
      activeSel = range;
      paintRange(range);
    }
    // 拖选期间把指针位置攒起来,一帧只算一次。
    // pointermove 一秒能来上百个,每个都做一次 caretRangeFromPoint 命中测试(强制布局)
    // 再重画一遍选区,这就是拖选"发涩"的根源。
    let pendingPoint = null;
    let selectFrame = 0;

    function flushSelect() {
      if (selectFrame) { cancelAnimationFrame(selectFrame); selectFrame = 0; }
      const p = pendingPoint;
      pendingPoint = null;
      // 只在拖选进行中补画:双击选词是另一条路径,别用积压的拖拽点把它覆盖掉
      if (p && selecting) updateSelect(p.x, p.y);
    }

    function scheduleSelect(x, y) {
      pendingPoint = { x, y };
      if (selectFrame) return;
      selectFrame = requestAnimationFrame(() => {
        selectFrame = 0;
        const p = pendingPoint;
        pendingPoint = null;
        if (p) updateSelect(p.x, p.y);
      });
    }

    // 拖到窗口上下边缘时自动滚屏 —— 长段落一口气拖不到底,不滚就等于选不中
    const EDGE_PX = 56;        // 离边缘多近开始滚
    const EDGE_SPEED = 12;     // 每帧滚多少像素
    let lastX = 0, lastY = 0;
    let autoDir = 0;           // -1 向上, +1 向下, 0 不动
    let autoFrame = 0;

    function autoScrollTick() {
      autoFrame = 0;
      if (!selecting || !autoDir) return;
      const before = window.scrollY;
      window.scrollBy(0, autoDir * EDGE_SPEED);
      if (window.scrollY === before) { autoDir = 0; return; }  // 已经到顶/到底,停
      scheduleSelect(lastX, lastY);   // 屏幕点位没变,底下的文字换了 → 选区跟着延伸
      autoFrame = requestAnimationFrame(autoScrollTick);
    }

    function stopAutoScroll() {
      autoDir = 0;
      if (autoFrame) { cancelAnimationFrame(autoFrame); autoFrame = 0; }
    }

    // force 供"双击选词"使用:那条路径不经过拖选,走到这里时 selecting 早被 pointerup
    // 置成 false 了,于是下面直接 return —— 结果是双击只画出高亮、取词工具条永远不出现
    // (普通词尤其明显:点了完全没反应)。判断写成 === true,因为本函数还直接挂在
    // pointerup/pointercancel 上当监听器用,那里第一个参数是事件对象(truthy)。
    function endSelect(force) {
      flushSelect();          // 补上最后一帧还没画的选区,终点不能丢
      stopAutoScroll();
      const was = selecting;
      selecting = false;
      document.body.classList.remove("is-selecting");
      if (longPress) { clearTimeout(longPress); longPress = null; }
      if (!was && force !== true) return;   // 只在一次真正拖选结束时动作,避免误触复发
      if (!activeSel) { clearSel(); return; }
      const raw = activeSel.toString().replace(/\s+/g, " ").trim();
      // 只要含英文字母、且不含中日韩文字即可 —— 允许标点(逗号/斜杠/引号/括号等)
      const cjk = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;
      if (!raw || raw.length > 80 || cjk.test(raw) || !/[A-Za-z]/.test(raw)) {
        clearSel(); activeSel = null; return;
      }
      const sn = activeSel.startContainer;
      const el = sn.nodeType === 1 ? sn : sn.parentElement;
      const p = (el && (el.closest("p") || el.closest(".entry-body"))) || bodyEl;
      const rect = activeSel.getBoundingClientRect();
      const word = raw.toLowerCase();
      active = { el: null, anchor: rect, word, ctx: p ? p.textContent.trim().slice(0, 240) : "" };
      setVocabContext(word, "", "", active.ctx);
      showAt(rect,
        `<div class="vp-word">${escapeHtml(raw)}` +
        `<button class="vp-close" data-close-popup title="Close">✕</button></div>` +
        `<div class="vp-sel-actions">` +
        `<button class="vp-trans-btn" data-selection-lookup>Translate</button>` +
        `<button class="vp-trans-btn" data-selection-copy>Copy</button>` +
        `<button class="vp-add" data-add-vocab>＋ Add to my words</button></div>`);
    }
    async function selectionLookup(btn) {
      if (!active) return;
      const word = active.word;
      btn.disabled = true;
      btn.textContent = "翻译中…";
      const res = await browserTranslate(word);
      if (!active || active.word !== word) return;
      if (res && res.zh) {
        drawCard(active.anchor, { word, phonetic: "", definition_en: "",
                                  translation: res.zh, cefr_level: "" }, "", true);
        return;
      }
      // 内置引擎不可用时退到在线词典(用户主动点击才烧 AI 额度)
      try {
        const q = active.ctx ? `&sentence=${encodeURIComponent(active.ctx)}` : "";
        const r = await fetch(`/api/dict?word=${encodeURIComponent(word)}${q}`);
        const info = await r.json();
        if (!info || !(info.translation || info.definition_en)) throw new Error("empty");
        drawCard(active.anchor, { word, phonetic: info.phonetic || "",
                                  definition_en: info.definition_en || "",
                                  translation: info.translation || "",
                                  cefr_level: info.cefr_level || "" }, "", true);
      } catch (_) {
        btn.disabled = false;
        btn.textContent = "翻译失败，重试";
      }
    }
    async function copySelection(btn) {
      const text = (activeSel && activeSel.toString()) || (active && active.word) || "";
      if (!text) return;
      try {
        await navigator.clipboard.writeText(text);
      } catch (_) {                      // 非安全上下文/无权限时的退路
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.cssText = "position:fixed;opacity:0";
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand("copy"); } catch (e) {}
        ta.remove();
      }
      btn.textContent = "Copied ✓";
      setTimeout(() => { btn.textContent = "Copy"; }, 1200);
    }

    if (bodyEl) {
      bodyEl.addEventListener("pointerdown", e => {
        if (e.pointerType === "mouse" && e.button !== 0) return;
        if (e.pointerType === "touch") {          // 触摸:长按 400ms 才进入划词
          longPress = setTimeout(() => { longPress = null; beginSelect(e.clientX, e.clientY); }, 400);
          return;
        }
        beginSelect(e.clientX, e.clientY);
      });
      bodyEl.addEventListener("pointermove", e => {
        if (selecting) {
          lastX = e.clientX; lastY = e.clientY;
          scheduleSelect(e.clientX, e.clientY);
          const h = window.innerHeight;
          autoDir = e.clientY < EDGE_PX ? -1 : (e.clientY > h - EDGE_PX ? 1 : 0);
          if (autoDir && !autoFrame) autoFrame = requestAnimationFrame(autoScrollTick);
          return;
        }
        if (longPress) { clearTimeout(longPress); longPress = null; }  // 一动即视为滚动
      });
      // 触摸划词期间阻止页面滚动(必须非 passive 才能 preventDefault)
      bodyEl.addEventListener("touchmove", e => { if (selecting) e.preventDefault(); }, { passive: false });
      // 双击选词
      bodyEl.addEventListener("dblclick", e => {
        const r = caretRangeAt(e.clientX, e.clientY);
        if (!r || !inBody(r.startContainer)) return;
        const t = r.startContainer;
        if (t.nodeType !== 3) return;
        const txt = t.textContent;
        const isW = c => /[A-Za-z'’\-]/.test(c);
        let s = r.startOffset, en = r.startOffset;
        while (s > 0 && isW(txt[s - 1])) s--;
        while (en < txt.length && isW(txt[en])) en++;
        if (s === en) return;
        const range = document.createRange();
        range.setStart(t, s);
        range.setEnd(t, en);
        activeSel = range;
        paintRange(range);
        endSelect(true);      // true = 这条路径不是拖选,但同样要弹出取词工具条
      });
      // 注意:这两处直接挂监听器,回调会收到事件对象 —— endSelect 内部用 === true 区分
      window.addEventListener("pointerup", endSelect);
      window.addEventListener("pointercancel", endSelect);
    }

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
      // 兼容早期带"（浏览器翻译）"标注的数据(现在展示时不再加该标注)
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