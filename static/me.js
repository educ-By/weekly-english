/* me.js — 个人页:填充 生词本 / 阅读进度 / 阅读历史 三个区块 */
(() => {
  const token = localStorage.getItem("we_token");
  if (!token) {
    location.href = "/auth/login";
    return;
  }
  const headers = { "Authorization": "Bearer " + token };

  const articleHref = (issueKey, articleId) =>
    `/issue/${encodeURIComponent(issueKey)}/article-${encodeURIComponent(articleId)}.html`;

  function setH2(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function slot(id) {
    return document.getElementById(id);
  }

  async function load() {
    try {
      const me = await fetch("/auth/me", { headers }).then(r => {
        if (!r.ok) throw new Error("not logged in");
        return r.json();
      });
      document.getElementById("me-title").textContent = me.email;
      document.getElementById("me-meta").innerHTML =
        `<span>Member since ${new Date(me.created_at).toLocaleDateString()}</span>` +
        `<span class="dot"></span>` +
        `<a class="nav-link" href="/auth/logout">Sign out</a>`;

      const [vocabData, progData, histData] = await Promise.all([
        fetch("/api/v1/vocab", { headers }).then(r => r.json()).catch(() => ({})),
        fetch("/api/v1/progress", { headers }).then(r => r.json()).catch(() => ({})),
        fetch("/api/v1/history", { headers }).then(r => r.json()).catch(() => ({})),
      ]);
      renderVocab(vocabData.entries || []);
      renderProgress(progData.progress || []);
      renderHistory(histData.entries || []);
    } catch (e) {
      localStorage.clear();
      location.href = "/auth/login";
    }
  }

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

  function renderProgress(rows) {
    setH2("me-progress-h2", `Reading progress · ${rows.length} articles`);
    const box = slot("me-progress");
    if (!box) return;
    if (!rows.length) {
      box.innerHTML = `<p class="me-empty">No reading data yet. Open any article while
        signed in and your progress will show up here.</p>`;
      return;
    }
    const list = document.createElement("div");
    list.className = "progress-list";
    rows.slice().sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""))
      .forEach(p => {
        const item = document.createElement("a");
        item.className = "progress-row";
        item.href = articleHref(p.issue_key, p.article_id);
        const mins = Math.round((p.seconds_read || 0) / 60);
        item.innerHTML = `
          <span class="progress-issue">${escape(p.issue_key)}</span>
          <span class="progress-pct">${Math.round(p.scroll_pct || 0)}%</span>
          <span class="progress-time">${mins} min</span>
          <span class="progress-state">${p.completed ? "Completed" : "In progress"}</span>
        `;
        list.appendChild(item);
      });
    box.innerHTML = "";
    box.appendChild(list);
  }

  function renderHistory(entries) {
    // 每打开一次就写一行 —— 按文章去重,只留最近一次
    const seen = new Set();
    const rows = [];
    entries.forEach(e => {
      const k = `${e.issue_key}|${e.article_id}`;
      if (seen.has(k)) return;
      seen.add(k);
      rows.push(e);
    });

    setH2("me-history-h2", `Reading history · ${rows.length} articles`);
    const box = slot("me-history");
    if (!box) return;
    if (!rows.length) {
      box.innerHTML = `<p class="me-empty">Nothing opened yet.</p>`;
      return;
    }
    const list = document.createElement("div");
    list.className = "progress-list";
    rows.forEach(e => {
      const item = document.createElement("a");
      item.className = "progress-row history-row";
      item.href = articleHref(e.issue_key, e.article_id);
      const when = e.opened_at ? new Date(e.opened_at).toLocaleDateString() : "";
      item.innerHTML = `
        <span class="progress-issue">${escape(e.issue_key)}</span>
        <span class="history-title">${escape(e.title || "(untitled)")}</span>
        <span class="progress-time">${escape(when)}</span>
      `;
      list.appendChild(item);
    });
    box.innerHTML = "";
    box.appendChild(list);
  }

  function escape(s) {
    return String(s || "").replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;",
      '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  load();
})();
