/* me.js — 个人页:昵称 / 生词本 / 阅读(进度 + 历史合并成一栏)/ 学习概览 */
(() => {
  const token = localStorage.getItem("we_token");
  if (!token) {
    location.href = "/auth/login";
    return;
  }
  const headers = { "Authorization": "Bearer " + token };

  let me = null;   // 当前用户;改完昵称要重画头部和顶栏

  const articleHref = (issueKey, articleId) =>
    `/issue/${encodeURIComponent(issueKey)}/article-${encodeURIComponent(articleId)}.html`;

  function setH2(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }
  function slot(id) {
    return document.getElementById(id);
  }
  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d.getTime()) ? "" : d.toLocaleDateString();
  }

  /* ---------------- 头部 + 昵称 ---------------- */
  function renderHeader() {
    const title = document.getElementById("me-title");
    const meta = document.getElementById("me-meta");
    const nick = (me.display_name || "").trim();
    if (!title || !meta) return;

    // 标题优先显示昵称;没设过就还是邮箱
    title.textContent = nick || me.email;

    const parts = [];
    // 只在用了昵称时才附带邮箱,否则标题那行已经就是邮箱,会重复
    if (nick) parts.push(`<span>${escape(me.email)}</span>`);
    parts.push(`<span>Member since ${fmtDate(me.created_at)}</span>`);
    parts.push(`<button class="linklike" type="button" id="me-edit-name">`
             + `${nick ? "Edit name" : "Add a name"}</button>`);
    parts.push(`<a class="nav-link" href="/auth/logout">Sign out</a>`);
    meta.innerHTML = parts.join('<span class="dot"></span>');

    const edit = document.getElementById("me-edit-name");
    if (edit) edit.addEventListener("click", openNameForm);
  }

  // 注意:/me 页不加载 app.js,顶栏那个 nav-item 是本页的"当前区块"标识
  // (模板写死 "My learning"),不该被昵称顶掉 —— 所以这里不碰它。
  // 其它页面由 app.js 的 bindAccountLink 负责显示昵称。

  function openNameForm() {
    const form = document.getElementById("me-name");
    if (!form) return;
    form.elements.display_name.value = me.display_name || "";
    const help = document.getElementById("me-name-help");
    if (help) help.textContent = "";
    form.hidden = false;
    form.elements.display_name.focus();
    form.elements.display_name.select();
  }

  function bindNameForm() {
    const form = document.getElementById("me-name");
    if (!form) return;
    const help = document.getElementById("me-name-help");
    const cancel = form.querySelector("[data-cancel-name]");
    if (cancel) cancel.addEventListener("click", () => {
      form.hidden = true;
      if (help) help.textContent = "";
    });

    form.addEventListener("submit", async e => {
      e.preventDefault();
      const btn = form.querySelector(".me-name-save");
      const value = form.elements.display_name.value.trim();
      if (btn) btn.disabled = true;
      if (help) help.textContent = "";
      try {
        const r = await fetch("/api/v1/profile", {
          method: "POST",
          headers: { ...headers, "Content-Type": "application/json" },
          body: JSON.stringify({ display_name: value }),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || "Could not save the name.");
        if (data.user) me = data.user;
        renderHeader();
        form.hidden = true;
      } catch (err) {
        if (help) help.textContent = String(err.message || err);
      } finally {
        if (btn) btn.disabled = false;
      }
    });
  }

  /* ---------------- 加载 ---------------- */
  async function load() {
    try {
      me = await fetch("/auth/me", { headers }).then(r => {
        if (!r.ok) throw new Error("not logged in");
        return r.json();
      });
      renderHeader();
      bindNameForm();

      const [vocabData, progData, histData] = await Promise.all([
        fetch("/api/v1/vocab", { headers }).then(r => r.json()).catch(() => ({})),
        fetch("/api/v1/progress", { headers }).then(r => r.json()).catch(() => ({})),
        fetch("/api/v1/history", { headers }).then(r => r.json()).catch(() => ({})),
      ]);
      const vocab = vocabData.entries || [];
      const rows = mergeReading(progData.progress || [], histData.entries || []);
      renderStats(vocab, rows);
      renderVocab(vocab);
      renderReading(rows);
    } catch (e) {
      localStorage.clear();
      location.href = "/auth/login";
    }
  }

  /* ---------------- 阅读:进度 + 历史合并 ---------------- */
  // 两栏原本内容几乎一样(都是"我读过的文章"),所以并成一栏:
  // 进度贡献 百分比/时长/完成度,历史贡献 标题兜底 与 最近打开时间,按 (期号, 文章) 合并。
  function mergeReading(progress, history) {
    const byKey = new Map();
    const keyOf = x => `${x.issue_key}|${x.article_id}`;
    const at = x => x || { issue_key: "", article_id: "" };

    history.forEach(h => {
      const k = keyOf(h);
      const row = byKey.get(k) || at(h);
      if (!row.lastOpened) row.lastOpened = h.opened_at;   // 历史按时间倒序,首条即最近
      if (!row.title) row.title = h.title || "";
      byKey.set(k, row);
    });

    progress.forEach(p => {
      const k = keyOf(p);
      const row = byKey.get(k) || at(p);
      row.title = p.title || row.title || "";
      row.pct = p.scroll_pct;
      row.seconds = p.seconds_read || 0;
      row.completed = p.completed;
      row.updated_at = p.updated_at;
      byKey.set(k, row);
    });

    return Array.from(byKey.values())
      .map(r => { r.sortKey = r.updated_at || r.lastOpened || ""; return r; })
      .sort((a, b) => String(b.sortKey).localeCompare(String(a.sortKey)));
  }

  function renderStats(vocab, rows) {
    const box = slot("me-stats");
    if (!box) return;
    const completed = rows.filter(r => r.completed).length;
    const minutes = Math.round(rows.reduce((s, r) => s + (r.seconds || 0), 0) / 60);

    // 全新账户给一排 0 没有意义,不如不显示这块
    if (!rows.length && !vocab.length) {
      box.hidden = true;
      return;
    }

    const cells = [
      [rows.length, rows.length === 1 ? "article read" : "articles read"],
      [completed, "completed"],
      [minutes, minutes === 1 ? "minute read" : "minutes read"],
      [vocab.length, vocab.length === 1 ? "word saved" : "words saved"],
    ];
    box.innerHTML = cells.map(([n, label]) =>
      `<div class="me-stat"><span class="me-stat-num">${n}</span>` +
      `<span class="me-stat-label">${escape(label)}</span></div>`
    ).join("");
    box.hidden = false;
  }

  function renderReading(rows) {
    setH2("me-reading-h2", `Reading · ${rows.length} article${rows.length === 1 ? "" : "s"}`);
    const box = slot("me-reading");
    if (!box) return;
    if (!rows.length) {
      box.innerHTML = `<p class="me-empty">Nothing yet. Open any article while signed in
        and it will show up here with how far you got.</p>`;
      return;
    }
    const list = document.createElement("div");
    list.className = "progress-list";
    rows.forEach(r => {
      const item = document.createElement("a");
      item.className = "progress-row reading-row";
      item.href = articleHref(r.issue_key, r.article_id);
      // 只在历史里出现过的文章(打开但还没读满时长)没有进度数据,给破折号而不是 0%
      const tracked = typeof r.pct === "number";
      const mins = tracked ? Math.round((r.seconds || 0) / 60) : null;
      item.innerHTML = `
        <span class="progress-issue">${escape(r.issue_key)}</span>
        <span class="progress-title" title="${escape(r.title || r.article_id)}">${escape(r.title || r.article_id)}</span>
        <span class="progress-pct">${tracked ? Math.round(r.pct) + "%" : "—"}</span>
        <span class="progress-time">${mins === null ? "—" : mins + " min"}</span>
        <span class="progress-state">${tracked ? (r.completed ? "Completed" : "In progress") : "Opened"}</span>
        <span class="progress-time reading-when">${escape(fmtDate(r.sortKey))}</span>
      `;
      list.appendChild(item);
    });
    box.innerHTML = "";
    box.appendChild(list);
  }

  /* ---------------- 生词本 ---------------- */
  function renderVocab(entries) {
    setH2("me-vocab-h2", `My vocabulary · ${entries.length} words`);
    const box = slot("me-vocab");
    if (!box) return;
    if (!entries.length) {
      box.innerHTML = `<p class="me-empty">No words yet. Open an article and click any
        out-of-scope word, then choose "Add to my words".</p>`;
      return;
    }
    const list = document.createElement("div");
    list.className = "vocab-list-cards";
    entries.forEach(e => {
      const card = document.createElement("div");
      card.className = "vocab-card";
      card.innerHTML = `
        <div class="vocab-card-head">
          <span class="vocab-card-word">${escape(e.word)}</span>
          <button class="vocab-remove" data-id="${e.id}">Remove</button>
        </div>
        ${e.translation ? `<div class="vocab-card-trans">${escape(e.translation)}</div>` : ""}
        ${e.definition ? `<div class="vocab-card-def">${escape(e.definition)}</div>` : ""}
        ${e.sentence ? `<blockquote class="vocab-card-sentence">${escape(e.sentence)}</blockquote>` : ""}
        ${e.source_issue ? `<div class="vocab-card-src">From ${escape(e.source_issue)}</div>` : ""}
      `;
      list.appendChild(card);
    });
    box.innerHTML = "";
    box.appendChild(list);
    box.querySelectorAll(".vocab-remove").forEach(b => {
      b.addEventListener("click", async () => {
        const id = b.dataset.id;
        await fetch("/api/v1/vocab/" + id, { method: "DELETE", headers });
        b.closest(".vocab-card").remove();
      });
    });
  }

  function escape(s) {
    return String(s || "").replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;",
      '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  load();
})();
