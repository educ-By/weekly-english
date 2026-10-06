/* 雅思专区交互 —— 词表搜索/发音/收藏、闪卡单轮会话、AI 写作批改与口语模拟。
   约定:所有网络调用都返回结构化结果(失败也返回对象,不抛),调用方据此恢复
   按钮状态 —— 以前 fetch 一 reject 按钮就永久停在 "批改中…"。 */
(function () {
  "use strict";
  const page = document.body.dataset.page;
  const token = localStorage.getItem("we_token");
  const authHeaders = token ? { "Authorization": "Bearer " + token } : {};

  function needLogin() {
    if (!confirm("这个功能需要登录。现在去登录?")) return false;
    location.href = "/auth/login";
    return false;
  }

  // 发音开关:记住用户选择,与 app.js 的整段朗读共用同一个 window.speechSynthesis
  function soundOn(key) {
    const el = document.getElementById(key);
    return !el || el.checked;
  }
  const soundBtn = (key) => document.getElementById(key);

  function say(word, soundKey) {
    if (!soundOn(soundKey)) return;
    try {
      const u = new SpeechSynthesisUtterance(word);
      u.lang = "en-GB";
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
    } catch (e) { /* 无 TTS 时静默 */ }
  }

  function stopSpeech() {
    try { speechSynthesis.cancel(); } catch (e) { /* ignore */ }
  }

  async function postJSON(url, body) {
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders },
        body: JSON.stringify(body),
      });
      if (r.status === 401) { needLogin(); return { ok: false, unauthorized: true }; }
      const data = await r.json().catch(() => null);
      if (!data) return { ok: false, content: "服务器返回了无法解析的内容,请重试。" };
      return data;
    } catch (e) {
      return { ok: false, content: "网络异常,请检查连接后重试。" };
    }
  }

  async function getJSON(url) {
    try {
      const r = await fetch(url, { headers: authHeaders, cache: "no-store" });
      if (!r.ok) return null;
      return await r.json();
    } catch (e) {
      return null;
    }
  }

  function el(tag, cls, text) {
    const d = document.createElement(tag);
    if (cls) d.className = cls;
    if (text != null) d.textContent = text;
    return d;
  }

  function alertBox(id, text, kind) {
    const box = document.getElementById(id);
    if (!box) return;
    box.textContent = text || "";
    box.className = "ielts-alert" + (kind ? " is-" + kind : "");
    box.hidden = !text;
  }

  /* 把后端返回的 ok/refused 区分开 —— 以前额度用完和正常反馈长得一模一样 */
  function failureText(out) {
    if (!out) return "请求失败,请重试。";
    if (out.unauthorized) return "";
    if (out.refused) return "这个问题超出了雅思辅导的范围,换一个备考相关的问题试试。";
    return out.content || "请求失败,请重试。";
  }

  function countWords(text) {
    const m = (text || "").match(/[A-Za-z0-9'’-]+/g);
    return m ? m.length : 0;
  }

  /* ---------- 音频转码:任意录音 → 16k / 单声道 / 16bit WAV ----------
     智聆只吃这个规格,而 MediaRecorder 给的是 webm/opus。用 Web Audio 解码后
     交给 OfflineAudioContext 以 16k 重采样(浏览器自带的重采样质量够用),
     再手写 WAV 头。全程在浏览器本地完成,不依赖服务器上的 ffmpeg。 */
  async function toWav16k(blob) {
    const AC = window.AudioContext || window.webkitAudioContext;
    const OAC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!AC || !OAC) throw new Error("浏览器不支持音频转码");
    const ctx = new AC();
    let decoded;
    try {
      decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
    } finally {
      if (ctx.close) ctx.close();
    }
    const rate = 16000;
    const frames = Math.max(1, Math.ceil(decoded.duration * rate));
    const off = new OAC(1, frames, rate);
    const src = off.createBufferSource();
    src.buffer = decoded;
    src.connect(off.destination);
    src.start();
    const rendered = await off.startRendering();
    return encodeWav16(rendered.getChannelData(0), rate);
  }

  function encodeWav16(samples, rate) {
    const n = samples.length;
    const buf = new ArrayBuffer(44 + n * 2);
    const view = new DataView(buf);
    const put = (off, str) => {
      for (let i = 0; i < str.length; i++) view.setUint8(off + i, str.charCodeAt(i));
    };
    put(0, "RIFF");
    view.setUint32(4, 36 + n * 2, true);
    put(8, "WAVE");
    put(12, "fmt ");
    view.setUint32(16, 16, true);      // fmt 块长度
    view.setUint16(20, 1, true);       // 1 = PCM
    view.setUint16(22, 1, true);       // 单声道
    view.setUint32(24, rate, true);
    view.setUint32(28, rate * 2, true);  // 字节率
    view.setUint16(32, 2, true);       // 块对齐
    view.setUint16(34, 16, true);      // 位深
    put(36, "data");
    view.setUint32(40, n * 2, true);
    let off = 44;
    for (let i = 0; i < n; i++, off += 2) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([view], { type: "audio/wav" });
  }

  function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => {
        const s = String(r.result || "");
        const i = s.indexOf(",");
        i >= 0 ? resolve(s.slice(i + 1)) : reject(new Error("读取音频失败"));
      };
      r.onerror = () => reject(new Error("读取音频失败"));
      r.readAsDataURL(blob);
    });
  }

  // ---------------- 词表页 ----------------
  if (page === "ielts-vocab") {
    bindWordList();

    function bindWordList() {
      const wrap = document.getElementById("vw-words");
      if (!wrap) return;

      wrap.addEventListener("click", async (ev) => {
        const btn = ev.target.closest(".ielts-btn");
        if (!btn) return;
        const li = btn.closest(".ielts-word");
        const word = li && li.dataset.word;
        if (!word) return;
        if (btn.dataset.act === "say") { say(word, "vw-sound"); return; }
        if (btn.dataset.act === "save") {
          if (!token) { needLogin(); return; }
          const trans = (li.querySelector(".ielts-trans") || {}).textContent || "";
          btn.disabled = true;
          const out = await postJSON("/api/v1/vocab", {
            word: word, translation: trans.trim(), source_issue: "ielts",
          });
          if (out && out.ok) {
            btn.textContent = out.updated ? "✓ 已收藏" : "✓ 已收藏";
          } else {
            btn.textContent = "重试";
            btn.disabled = false;
          }
        }
      });
    }

    // ---- 掌握状态(登录后一次拉全量,建 map 标注每行) ----
    async function markMastery() {
      if (!token) return;
      const data = await getJSON("/api/ielts/progress");
      if (!data) return;
      const map = new Map((data.items || []).map((i) => [i.word, i.box || 0]));
      document.querySelectorAll(".ielts-word").forEach((li) => {
        const box = map.get(li.dataset.word);
        const slot = li.querySelector(".ielts-box");
        if (box == null || !slot) return;
        slot.hidden = false;
        slot.textContent = box >= 3 ? "已掌握" : "box " + box + "/3";
        slot.className = "ielts-box" + (box >= 3 ? " is-mastered" : "");
      });
      const mastered = document.getElementById("vw-mastered");
      if (mastered && data.mastered) {
        mastered.hidden = false;
        mastered.textContent = "已掌握 " + data.mastered + " 词";
        const dot = document.getElementById("vw-mastered-wrap");
        if (dot) dot.hidden = false;
      }
    }
    markMastery();

    // ---- AJAX 搜索/翻页(无 JS 时表单与链接仍然可用) ----
    const form = document.getElementById("vw-form");
    const input = document.getElementById("vw-input");

    function renderList(data) {
      const words = document.getElementById("vw-words");
      const pager = document.getElementById("vw-pager");
      const total = document.getElementById("vw-total");
      if (total) {
        total.textContent = data.total + " word" + (data.total === 1 ? "" : "s");
      }
      if (!data.items || !data.items.length) {
        words.innerHTML = '<p class="empty">No words match “' +
          (data.q || "") + '”.</p>';
        pager.hidden = true;
        return;
      }
      const ul = el("ul", "ielts-wordlist");
      data.items.forEach((w) => {
        const li = el("li", "ielts-word");
        li.dataset.word = w.word;
        const main = el("div", "ielts-word-main");
        main.appendChild(el("span", "ielts-word-text", w.word));
        if (w.phonetic) main.appendChild(el("span", "ielts-phon", "/" + w.phonetic + "/"));
        if (w.tags && w.tags.length) main.appendChild(el("span", "ielts-pos", w.tags.join(" · ")));
        const box = el("span", "ielts-box");
        box.hidden = true;
        main.appendChild(box);
        li.appendChild(main);
        if (w.translation) li.appendChild(el("p", "ielts-trans", w.translation));
        const acts = el("div", "ielts-word-actions");
        const bSay = el("button", "ielts-btn", "🔊");
        bSay.type = "button"; bSay.dataset.act = "say"; bSay.title = "发音";
        const bSave = el("button", "ielts-btn", "+ 生词本");
        bSave.type = "button"; bSave.dataset.act = "save"; bSave.title = "加入生词本";
        acts.appendChild(bSay); acts.appendChild(bSave);
        li.appendChild(acts);
        ul.appendChild(li);
      });
      words.innerHTML = "";
      words.appendChild(ul);
      renderPager(data);
      markMastery();
    }

    function renderPager(data) {
      const pager = document.getElementById("vw-pager");
      if (!pager) return;
      pager.hidden = false;
      pager.innerHTML = "";
      const mk = (p) => "/ielts/vocab?q=" + encodeURIComponent(data.q || "") + "&page=" + p;
      if (data.pages <= 1) {
        pager.appendChild(el("span", "ielts-page-info", "Page 1 / 1"));
        return;
      }
      if (data.pageno > 1) pager.appendChild(link("‹ Prev", mk(data.pageno - 1)));
      const window_ = (p) => p <= 2 || p > data.pages - 2 ||
        (p >= data.pageno - 2 && p <= data.pageno + 2);
      let gapShown = false;
      for (let p = 1; p <= data.pages; p++) {
        if (p === data.pageno) {
          const cur = el("span", "ielts-page-link is-on", String(p));
          cur.setAttribute("aria-current", "page");
          pager.appendChild(cur);
          gapShown = false;
        } else if (window_(p)) {
          pager.appendChild(link(String(p), mk(p)));
          gapShown = false;
        } else if (!gapShown) {
          pager.appendChild(el("span", "ielts-page-gap", "…"));
          gapShown = true;
        }
      }
      if (data.pageno < data.pages) pager.appendChild(link("Next ›", mk(data.pageno + 1)));
      pager.appendChild(el("span", "ielts-page-info",
        "Page " + data.pageno + " / " + data.pages));
    }

    function link(text, href) {
      const a = el("a", "ielts-page-link", text);
      a.href = href;
      return a;
    }

    async function navigate(q, p, push) {
      const data = await getJSON("/api/ielts/words?q=" +
        encodeURIComponent(q || "") + "&page=" + (p || 1));
      if (!data) return;
      renderList(data);
      const drill = document.getElementById("vw-drill");
      if (drill) {
        drill.href = "/ielts/flashcards" + (data.q ? "?q=" + encodeURIComponent(data.q) : "");
      }
      if (push) {
        history.pushState({ q: data.q, page: data.pageno }, "",
          "/ielts/vocab?q=" + encodeURIComponent(data.q || "") + "&page=" + data.pageno);
      }
    }

    if (form) {
      form.addEventListener("submit", (ev) => {
        ev.preventDefault();
        navigate(input.value.trim(), 1, true);
      });
      // 回车显式处理:隐式表单提交依赖浏览器实现,这里自己接住,行为确定
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          navigate(input.value.trim(), 1, true);
        }
      });
    }
    const pager = document.getElementById("vw-pager");
    if (pager) {
      pager.addEventListener("click", (ev) => {
        const a = ev.target.closest("a.ielts-page-link");
        if (!a) return;
        ev.preventDefault();
        const url = new URL(a.href, location.origin);
        navigate(url.searchParams.get("q") || "", parseInt(url.searchParams.get("page") || "1", 10), true);
      });
    }
    window.addEventListener("popstate", (ev) => {
      const st = ev.state;
      if (st) navigate(st.q || "", st.page || 1, false);
    });
  }

  // ---------------- 闪卡页 ----------------
  if (page === "ielts-flashcards") {
    const $ = (id) => document.getElementById(id);
    const SESSION = 20;

    /* 单轮会话模型:queue 里是本轮要看的卡;每张卡本轮只"判定"一次计入进度,
       不认识的排到队尾再多看一遍(标记 _retry,不再计入进度)。这样一轮一定
       会结束 —— 早先的写法把"认识但还没到 box 3"的卡也一直塞回队列,勾上
       "连已掌握的词一起复习"就变成永远转不完的循环。 */
    let queue = [];
    let total = 0;
    let seen = 0;          // 本轮已判定的张数(首次判定才计)
    let missed = [];       // 本轮答错的词
    let cur = null;
    let busy = false;

    const scope = (document.body.dataset.q || "").trim();

    function status(text) { $("fc-status").textContent = text; }

    function setMastered(n) {
      if (typeof n !== "number") return;
      $("fc-mastered").hidden = false;
      $("fc-mastered").textContent = "已掌握 " + n + " 词";
      $("fc-mastered-wrap").hidden = false;
    }

    function showSummary() {
      $("fc-card").hidden = true;
      $("fc-controls").hidden = true;
      $("fc-summary").hidden = false;
      $("fc-summary-title").textContent = "本轮完成";
      $("fc-summary-body").textContent =
        "看完 " + total + " 个词 — 认识 " + (total - missed.length) +
        " 个,不认识 " + missed.length + " 个。" +
        (missed.length ? "答错的词已按实际表现回退,下次还会出现。" : "全部认识。");
      $("fc-review-missed").hidden = missed.length === 0;
      $("fc-progress").textContent = "";
      status("本轮完成");
    }

    function show() {
      $("fc-summary").hidden = true;
      $("fc-card").hidden = false;
      $("fc-controls").hidden = false;
      if (!queue.length) { showSummary(); return; }
      cur = queue[0];
      $("fc-word").textContent = cur.word;
      $("fc-word-back").textContent = cur.word;
      $("fc-phon").textContent = cur.phonetic ? "/" + cur.phonetic + "/" : "";
      $("fc-pos").textContent = (cur.tags && cur.tags.length) ? cur.tags.join(" · ") : "";
      $("fc-trans").textContent = cur.translation || "(词典暂无释义)";
      $("fc-card").classList.remove("is-flipped");
      status((cur.box >= 3 ? "已掌握 · " : "") + "box " + cur.box + "/3");
      $("fc-progress").textContent = cur._retry
        ? "回看本轮答错的词"
        : "第 " + Math.min(seen + 1, total) + " / " + total + " 个";
      say(cur.word, "fc-sound");
    }

    function flip() {
      if (!$("fc-summary").hidden || !cur) return;
      $("fc-card").classList.toggle("is-flipped");
    }

    async function judge(known) {
      if (!cur || busy) return;
      busy = true;
      const card = cur;
      if (!card._retry) {
        seen++;
        if (!known) missed.push(card.word);
      }
      if (token) {
        const out = await postJSON("/api/ielts/progress",
          { word: card.word, known: known });
        if (out && typeof out.box === "number") card.box = out.box;
        if (out) setMastered(out.mastered);
      }
      queue.shift();
      // 答错且这是第一次:排到队尾再看一遍;第二遍仍错就直接移出本轮
      if (!known && !card._retry) {
        card._retry = true;
        queue.push(card);
      }
      busy = false;
      show();
    }

    async function load(cards) {
      $("fc-summary").hidden = true;
      $("fc-card").hidden = false;
      $("fc-controls").hidden = false;
      $("fc-review-missed").hidden = true;
      status("Loading…");
      let items = null;
      if (cards) {
        items = cards.slice();
      } else {
        const url = "/api/ielts/drill?limit=" + SESSION +
          (scope ? "&q=" + encodeURIComponent(scope) : "") +
          "&include_mastered=" + ($("fc-include-mastered").checked ? "1" : "0");
        const data = await getJSON(url);
        if (!data) { status("加载失败,请刷新重试"); return; }
        items = (data.items || []).slice();
        setMastered(data.mastered);
        $("fc-login-hint").hidden = !!data.logged_in;
        if (!items.length) {
          // 池子空了:多半是这个词表范围内的词都已经掌握
          total = 0; seen = 0; missed = [];
          status("没有待复习的词");
          $("fc-card").hidden = true;
          $("fc-controls").hidden = true;
          $("fc-summary").hidden = false;
          $("fc-summary-title").textContent = "这个词表范围已经背完了";
          $("fc-summary-body").textContent =
            "勾选下面的「连已掌握的词一起复习」可以全部再过一遍。";
          $("fc-review-missed").hidden = true;
          $("fc-progress").textContent = "";
          return;
        }
      }
      queue = items;
      total = queue.length;
      seen = 0;
      missed = [];
      show();
    }

    $("fc-card").addEventListener("click", (ev) => {
      if (ev.target.closest(".fc-say")) return;
      flip();
    });
    $("fc-say").addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (cur) say(cur.word, "fc-sound");
    });
    $("fc-known").addEventListener("click", () => judge(true));
    $("fc-unknown").addEventListener("click", () => judge(false));
    $("fc-again").addEventListener("click", () => load(null));
    $("fc-review-missed").addEventListener("click", () => {
      const again = missed.map((w) => ({ word: w, tags: [], translation: "", box: 0 }));
      load(again.length ? again : null);
    });
    $("fc-include-mastered").addEventListener("change", () => load(null));

    // 键盘:空格翻面,1 = 不认识,2 = 认识。
    // 判定目标前先确认 target 是元素 —— 事件直接派发在 document 上时
    // ev.target.matches 不存在,会让整条快捷键静默失效。
    document.addEventListener("keydown", (ev) => {
      const t = ev.target;
      if (t && t.matches && t.matches("input, textarea, select")) return;
      if (ev.key === " " || ev.code === "Space") { ev.preventDefault(); flip(); }
      else if (ev.key === "1") judge(false);
      else if (ev.key === "2") judge(true);
    });

    load(null);
  }

  // ---------------- AI 练习页 ----------------
  if (page === "ielts-practice") {
    const aiReady = document.body.dataset.aiReady === "1";

    document.querySelectorAll(".ielts-tab").forEach((t) => {
      t.addEventListener("click", () => {
        document.querySelectorAll(".ielts-tab").forEach((x) => x.classList.remove("is-on"));
        document.querySelectorAll(".ielts-pane").forEach((x) => x.classList.remove("is-on"));
        t.classList.add("is-on");
        document.getElementById("pane-" + t.dataset.tab).classList.add("is-on");
        stopSpeech();
      });
    });

    if (!token) {
      const wrap = document.getElementById("pr-history-link-wrap");
      if (wrap) wrap.hidden = true;
    }

    /* ---------- 写作 ---------- */
    const wrEssay = document.getElementById("wr-essay");
    const wrCount = document.getElementById("wr-count");
    const wrHint = document.getElementById("wr-hint");
    const wrBtn = document.getElementById("wr-submit");
    const wrOut = document.getElementById("wr-output");

    function updateCount() {
      const n = countWords(wrEssay.value);
      wrCount.textContent = n + " words";
      wrCount.className = "ielts-count" + (n >= 50 ? " is-ok" : "");
      wrHint.textContent = n >= 250
        ? "已达 Task 2 建议长度 (250+)"
        : "Task 2 建议 250 词以上 · 至少 50 词";
    }
    wrEssay.addEventListener("input", updateCount);
    updateCount();

    const SCORE_LABELS = {
      TR: "Task Response", CC: "Coherence", LR: "Lexical",
      GRA: "Grammar", OA: "总分",
    };

    function renderScores(scores) {
      const wrap = el("div", "wr-scores");
      const overall = el("div", "wr-overall");
      overall.appendChild(el("span", "wr-overall-num", String(scores.OA != null ? scores.OA : "–")));
      overall.appendChild(el("span", "wr-overall-label", "Overall Band"));
      wrap.appendChild(overall);
      const grid = el("div", "wr-score-grid");
      ["TR", "CC", "LR", "GRA"].forEach((k) => {
        const cell = el("div", "wr-score");
        cell.appendChild(el("span", "wr-score-num", String(scores[k] != null ? scores[k] : "–")));
        cell.appendChild(el("span", "wr-score-label", k));
        cell.title = SCORE_LABELS[k] || k;
        grid.appendChild(cell);
      });
      wrap.appendChild(grid);
      return wrap;
    }

    /* 把【主要问题】/【改进建议】/【提升到 7 分】切成小节的纯文本块 */
    function renderFeedback(text) {
      const holder = document.createElement("div");
      const parts = (text || "").split(/(?=【[^】]{1,20}】)/);
      parts.forEach((chunk) => {
        const t = chunk.trim();
        if (!t) return;
        const m = t.match(/^【([^】]{1,20})】([\s\S]*)$/);
        const block = el("div", "wr-block");
        if (m) {
          block.appendChild(el("h4", "wr-block-title", m[1]));
          block.appendChild(el("p", "wr-block-body", m[2].trim()));
        } else {
          block.appendChild(el("p", "wr-block-body", t));
        }
        holder.appendChild(block);
      });
      return holder;
    }

    wrBtn.addEventListener("click", async () => {
      const essay = wrEssay.value.trim();
      if (!aiReady) { alertBox("wr-alert", "AI 未配置,练习功能暂不可用。", "err"); return; }
      if (!token) { needLogin(); return; }
      if (countWords(essay) < 50) {
        alertBox("wr-alert", "作文太短:至少 50 词(现在 " + countWords(essay) + " 词)。", "err");
        return;
      }
      alertBox("wr-alert", "");
      wrBtn.disabled = true;
      wrOut.hidden = false;
      wrOut.textContent = "批改中…(整篇作文需要一点时间)";
      let out = null;
      try {
        out = await postJSON("/api/ielts/writing", {
          question: document.getElementById("wr-question").value,
          essay: essay,
        });
      } finally {
        wrBtn.disabled = false;
      }
      if (!out || !out.ok) {
        const msg = failureText(out);
        wrOut.hidden = true;
        if (msg) alertBox("wr-alert", msg, out && out.refused ? "warn" : "err");
        return;
      }
      wrOut.innerHTML = "";
      if (out.scores) wrOut.appendChild(renderScores(out.scores));
      wrOut.appendChild(renderFeedback(out.content));
      if (out.id) {
        alertBox("wr-alert", "已保存到 My learning,可随时回看这次批改。", "ok");
      }
    });

    /* ---------- 口语 ---------- */
    const spLog = document.getElementById("sp-log");
    const spInput = document.getElementById("sp-input");
    const spStart = document.getElementById("sp-start");
    const spSend = document.getElementById("sp-send");
    const spEnd = document.getElementById("sp-end");
    const spPart = document.getElementById("sp-part");
    const spMic = document.getElementById("sp-mic");
    const spRec = document.getElementById("sp-rec");
    let history = [];
    let speaking = false;   // 会话进行中

    function addMsg(role, text, speakIt) {
      const p = el("p", "sp-msg sp-" + role);
      const who = role === "examiner" ? "考官" : (role === "candidate" ? "你" : "");
      p.textContent = who ? who + ": " + text : text;
      spLog.appendChild(p);
      spLog.scrollTop = spLog.scrollHeight;
      if (speakIt) say(text, "sp-sound");
      return p;
    }

    function setSpeaking(on) {
      speaking = on;
      spStart.hidden = on;
      spSend.hidden = !on;
      spEnd.hidden = !on;
      spInput.disabled = !on;
      spMic.hidden = !(on && micSupported());
      spRec.hidden = !on;
    }

    async function turn(action) {
      spStart.disabled = spSend.disabled = spEnd.disabled = true;
      let out = null;
      try {
        out = await postJSON("/api/ielts/speaking", {
          part: parseInt(spPart.value, 10),
          action: action,
          history: history,
        });
      } finally {
        spStart.disabled = spSend.disabled = spEnd.disabled = false;
      }
      if (!out || !out.ok) {
        const msg = failureText(out);
        if (msg) alertBox("sp-alert", msg, out && out.refused ? "warn" : "err");
        return;
      }
      alertBox("sp-alert", "");
      const text = (out.content || "").trim();
      if (!text) { addMsg("examiner", "(AI 没有返回内容,请重试)"); return; }
      if (text.indexOf("===FEEDBACK===") !== -1) {
        addMsg("examiner", "— 考试结束,以下是反馈 —");
        addMsg("feedback", text.split("===FEEDBACK===")[1].trim());
        setSpeaking(false);
        if (out.id) alertBox("sp-alert", "本次口语记录已保存到 My learning。", "ok");
      } else {
        addMsg("examiner", text, true);
        history.push({ role: "examiner", text: text });
      }
    }

    /* ---- Part 2 计时:1 分钟准备 + 2 分钟陈述 ---- */
    const spTimer = document.getElementById("sp-timer");
    let timerId = null;

    function stopTimer() {
      if (timerId) { clearInterval(timerId); timerId = null; }
      spTimer.hidden = true;
    }

    function runTimer(label, seconds, next) {
      spTimer.hidden = false;
      document.getElementById("sp-timer-label").textContent = label;
      let left = seconds;
      const clock = document.getElementById("sp-timer-clock");
      const paint = () => {
        const m = Math.floor(left / 60);
        const s = String(left % 60).padStart(2, "0");
        clock.textContent = m + ":" + s;
      };
      paint();
      if (timerId) clearInterval(timerId);
      timerId = setInterval(() => {
        left -= 1;
        paint();
        if (left <= 0) {
          clearInterval(timerId);
          timerId = null;
          after();
        }
      }, 1000);
      function after() {
        if (next) next();
        else {
          spTimer.hidden = false;
          document.getElementById("sp-timer-label").textContent = "陈述时间到";
          clock.textContent = "0:00";
        }
      }
      document.getElementById("sp-timer-skip").onclick = () => {
        clearInterval(timerId);
        timerId = null;
        after();
      };
    }

    spStart.addEventListener("click", async () => {
      if (!aiReady) { alertBox("sp-alert", "AI 未配置,练习功能暂不可用。", "err"); return; }
      if (!token) { needLogin(); return; }
      history = [];
      spLog.innerHTML = "";
      alertBox("sp-alert", "");
      setSpeaking(true);
      addMsg("examiner", "(模拟开始)");
      if (parseInt(spPart.value, 10) === 2) {
        runTimer("准备时间", 60, () => {
          runTimer("陈述时间", 120, stopTimer);
        });
      }
      await turn("start");
    });

    spSend.addEventListener("click", async () => {
      const text = spInput.value.trim();
      if (!text) return;
      spInput.value = "";
      history.push({ role: "candidate", text: text });
      addMsg("candidate", text);
      await turn("answer");
    });

    spEnd.addEventListener("click", async () => {
      const text = spInput.value.trim();
      if (text) {
        history.push({ role: "candidate", text: text });
        addMsg("candidate", text);
        spInput.value = "";
      }
      stopTimer();
      stopSpeech();
      await turn("feedback");
    });

    spPart.addEventListener("change", stopTimer);

    /* ---- 浏览器语音识别(国内多为不可用,静默降级) ---- */
    function recognition() {
      const C = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!C) return null;
      const r = new C();
      r.lang = "en-GB";
      r.interimResults = false;
      r.maxAlternatives = 1;
      return r;
    }
    function micSupported() { return !!recognition(); }
    if (spMic) {
      spMic.addEventListener("click", () => {
        const r = recognition();
        if (!r) { spMic.hidden = true; return; }
        spMic.disabled = true;
        spMic.textContent = "🎤 识别中…";
        r.onresult = (ev) => {
          const text = (ev.results && ev.results[0] && ev.results[0][0])
            ? ev.results[0][0].transcript : "";
          if (text) spInput.value = (spInput.value + " " + text).trim();
        };
        r.onerror = () => {
          alertBox("sp-alert", "语音识别不可用(浏览器或网络限制),请直接打字作答。", "warn");
          spMic.hidden = true;
        };
        r.onend = () => {
          spMic.disabled = false;
          spMic.textContent = "🎤 语音输入";
        };
        try { r.start(); }
        catch (e) { spMic.hidden = true; }
      });
    }

    /* ---- 本地录音自查(不上传,权限被拒就禁用) ---- */
    const recBox = document.getElementById("sp-rec-box");
    const recAudio = document.getElementById("sp-rec-audio");
    const recDel = document.getElementById("sp-rec-del");
    let recorder = null;
    let chunks = [];
    let recUrl = null;

    if (spRec && navigator.mediaDevices && window.MediaRecorder) {
      spRec.addEventListener("click", async () => {
        if (recorder && recorder.state === "recording") {
          recorder.stop();
          return;
        }
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          chunks = [];
          recorder = new MediaRecorder(stream);
          recorder.ondataavailable = (ev) => { if (ev.data.size) chunks.push(ev.data); };
          recorder.onstop = () => {
            stream.getTracks().forEach((t) => t.stop());
            if (recUrl) URL.revokeObjectURL(recUrl);
            recUrl = URL.createObjectURL(new Blob(chunks, { type: "audio/webm" }));
            recAudio.src = recUrl;
            recAudio.hidden = false;
            recDel.hidden = false;
            document.getElementById("sp-rec-status").textContent = "录完了,自己听听看";
            spRec.textContent = "● 录音自查";
          };
          recorder.start();
          recBox.hidden = false;
          recAudio.hidden = true;
          recDel.hidden = true;
          document.getElementById("sp-rec-status").textContent = "录音中…再点一次停止";
          spRec.textContent = "■ 停止录音";
        } catch (e) {
          alertBox("sp-alert", "拿不到麦克风权限或设备不支持录音,这一步可以跳过。", "warn");
          spRec.hidden = true;
          recBox.hidden = true;
        }
      });
      recDel.addEventListener("click", () => {
        if (recUrl) { URL.revokeObjectURL(recUrl); recUrl = null; }
        recAudio.hidden = true;
        recDel.hidden = true;
        document.getElementById("sp-rec-status").textContent = "再录一次吧";
      });
    } else if (spRec) {
      spRec.hidden = true;
    }

    /* ---------- 朗读评测(腾讯智聆):真的听音频,给音素级反馈 ----------
       模板只在服务端配好了智聆时才渲染这一段,所以控件在就说明可用。 */
    const prText = document.getElementById("pr-text");
    if (prText) bindPronounce();

    function bindPronounce() {
      const prRecord = document.getElementById("pr-record");
      const prEvalBtn = document.getElementById("pr-eval");
      const prBox = document.getElementById("pr-box");
      const prAudio = document.getElementById("pr-audio");
      const prRedo = document.getElementById("pr-redo");
      const prResult = document.getElementById("pr-result");
      let prRecorder = null;
      let prChunks = [];
      let prBlob = null;
      let prUrl = null;

      const lastAnswer = () => {
        for (let i = history.length - 1; i >= 0; i--) {
          if (history[i].role === "candidate") return history[i].text;
        }
        return "";
      };

      function setStatus(text) {
        document.getElementById("pr-status").textContent = text || "";
      }

      document.getElementById("pr-use-answer").addEventListener("click", () => {
        const a = lastAnswer();
        if (!a) { alertBox("pr-alert", "还没有回答可以引用,直接在框里输入要朗读的英文。", "warn"); return; }
        prText.value = a;
        alertBox("pr-alert", "");
      });

      document.getElementById("pr-say").addEventListener("click", () => {
        const t = prText.value.trim();
        if (!t) { alertBox("pr-alert", "先填要朗读的文本。", "warn"); return; }
        say(t, "sp-sound");          // 先听一遍示范,再自己读
      });

      prRecord.addEventListener("click", async () => {
        if (prRecorder && prRecorder.state === "recording") { prRecorder.stop(); return; }
        if (!prText.value.trim()) {
          alertBox("pr-alert", "先填要朗读的文本(可以点「用我的回答」)。", "warn");
          return;
        }
        if (!navigator.mediaDevices || !window.MediaRecorder) {
          alertBox("pr-alert", "这个浏览器不支持录音。", "err");
          return;
        }
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          prChunks = [];
          prBlob = null;
          prRecorder = new MediaRecorder(stream);
          prRecorder.ondataavailable = (ev) => { if (ev.data.size) prChunks.push(ev.data); };
          prRecorder.onstop = () => {
            stream.getTracks().forEach((t) => t.stop());
            prBlob = new Blob(prChunks, { type: prRecorder.mimeType || "audio/webm" });
            if (prUrl) URL.revokeObjectURL(prUrl);
            prUrl = URL.createObjectURL(prBlob);
            prAudio.src = prUrl;
            prAudio.hidden = false;
            prRedo.hidden = false;
            prEvalBtn.hidden = false;
            prRecord.textContent = "● 重录";
            setStatus("录完了,先回听一遍,确认没问题再提交评测");
          };
          prRecorder.start();
          prBox.hidden = false;
          prAudio.hidden = true;
          prRedo.hidden = true;
          prEvalBtn.hidden = true;
          prResult.hidden = true;
          alertBox("pr-alert", "");
          prRecord.textContent = "■ 停止朗读";
          setStatus("录音中…读完点「停止朗读」");
        } catch (e) {
          alertBox("pr-alert", "拿不到麦克风权限。请允许本页使用麦克风后重试。", "err");
        }
      });

      prRedo.addEventListener("click", () => {
        prRecord.click();
      });

      prEvalBtn.addEventListener("click", async () => {
        if (!prBlob) return;
        const text = prText.value.trim();
        if (!text) { alertBox("pr-alert", "先填要朗读的文本。", "warn"); return; }
        const words = countWords(text);
        if (words > 120) {
          alertBox("pr-alert", "文本超过 120 词(" + words + " 词),请分几段分别评测。", "err");
          return;
        }
        prEvalBtn.disabled = true;
        prRecord.disabled = true;
        alertBox("pr-alert", "");
        prResult.hidden = false;
        prResult.textContent = "转码并评测中…";
        let out = null;
        try {
          const wav = await toWav16k(prBlob);
          const b64 = await blobToBase64(wav);
          setStatus("已转成 16k WAV(" + Math.round(wav.size / 1024) + " KB),正在评测…");
          out = await postJSON("/api/ielts/pronounce", { text: text, audio: b64 });
        } catch (e) {
          out = { ok: false, content: "音频处理失败:" + (e && e.message ? e.message : "未知错误") };
        } finally {
          prEvalBtn.disabled = false;
          prRecord.disabled = false;
        }
        if (!out || !out.ok) {
          prResult.hidden = true;
          const msg = failureText(out);
          setStatus("");
          if (msg) alertBox("pr-alert", msg, out && out.unauthorized ? "warn" : "err");
          return;
        }
        setStatus("");
        renderPronounce(prResult, out);
      });
    }

    /* 测试用的暴露点:转码链和结果渲染没法用无麦克风的环境走一遍 UI,但可以直接
       验输出规格与渲染结果。只用函数引用,生产逻辑不依赖它(与本文件既有的
       __bt 钩子同一路数)。 */
    window.__ieltsAudio = { toWav16k, encodeWav16, blobToBase64, countWords };
    window.__ieltsPronRender = renderPronounce;

    /* 分数的颜色档：≥80 好、60-79 一般、<60 差(0-100 分制,不是雅思 band) */
    function scoreClass(v) {
      if (v == null) return "";
      if (v >= 80) return " is-good";
      if (v >= 60) return " is-mid";
      return " is-bad";
    }

    function renderPronounce(host, out) {
      host.innerHTML = "";
      host.hidden = false;

      const wrap = el("div", "wr-scores");
      if (out.score != null) {
        const overall = el("div", "wr-overall");
        overall.appendChild(el("span", "wr-overall-num" + scoreClass(out.score), String(out.score)));
        overall.appendChild(el("span", "wr-overall-label", "总分 / 100"));
        wrap.appendChild(overall);
      }
      const grid = el("div", "wr-score-grid");
      [["准确度", out.accuracy, "发音是否准确(音素级)"],
       ["流利度", out.fluency != null ? Math.round(out.fluency * 100) : null, "节奏与连贯(0-100)"],
       ["完整度", out.completeness != null ? Math.round(out.completeness * 100) : null, "有没有漏读(0-100)"]]
        .forEach(([label, v, tip]) => {
          if (v == null) return;
          const cell = el("div", "wr-score");
          cell.appendChild(el("span", "wr-score-num" + scoreClass(v), String(v)));
          cell.appendChild(el("span", "wr-score-label", label));
          cell.title = tip;
          grid.appendChild(cell);
        });
      wrap.appendChild(grid);
      host.appendChild(wrap);

      // 逐词:按分数着色,hover 看该词读错的音素
      const words = out.words || [];
      if (words.length) {
        const block = el("div", "pr-block");
        block.appendChild(el("h4", "wr-block-title", "逐词发音"));
        const line = el("div", "pr-words");
        words.forEach((w) => {
          const bad = (w.mispronounced || []).length;
          const span = el("span", "pr-word" + scoreClass(w.accuracy) + (bad ? " has-issue" : ""));
          span.textContent = w.word;
          const bits = [];
          if (w.accuracy != null) bits.push("准确度 " + w.accuracy);
          if (w.match != null && w.match !== 0) bits.push("匹配状态 " + w.match);
          if (bad) {
            bits.push(...w.mispronounced.map((p) =>
              (p.reference && p.phone && p.reference !== p.phone)
                ? "读成了 /" + p.phone + "/,应为 /" + p.reference + "/"
                : "音素 /" + (p.phone || p.reference || "?") + "/ 不清晰"));
          }
          if (bits.length) span.title = bits.join(" · ");
          line.appendChild(span);
        });
        block.appendChild(line);
        host.appendChild(block);
      }

      // 读错的音素单独汇总 —— 这才是"真的听出问题"的地方
      const issues = [];
      words.forEach((w) => {
        (w.mispronounced || []).forEach((p) => {
          if (p.reference && p.phone && p.reference !== p.phone) {
            issues.push({ word: w.word, said: p.phone, should: p.reference });
          }
        });
      });
      const weak = words.filter((w) => w.accuracy != null && w.accuracy < 60)
        .map((w) => w.word);
      const block = el("div", "pr-block");
      block.appendChild(el("h4", "wr-block-title", "需要重点练的地方"));
      if (!issues.length && !weak.length) {
        block.appendChild(el("p", "wr-block-body",
          "没有明显的音素错误,发音整体清楚。"));
      } else {
        const ul = document.createElement("ul");
        ul.className = "pr-issues";
        issues.slice(0, 12).forEach((it) => {
          const li = document.createElement("li");
          li.textContent = it.word + ":/" + it.should + "/ 读成了 /" + it.said + "/";
          ul.appendChild(li);
        });
        if (weak.length) {
          const li = document.createElement("li");
          li.textContent = "发音含糊的词:" + weak.slice(0, 12).join("、");
          ul.appendChild(li);
        }
        block.appendChild(ul);
      }
      host.appendChild(block);
    }
  }
})();
