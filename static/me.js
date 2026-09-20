/* me.js — 个人页:显示用户、生词本、阅读进度 */
(() => {
  const token = localStorage.getItem("we_token");
  if (!token) {
    location.href = "/auth/login";
    return;
  }
  const headers = { "Authorization": "Bearer " + token };

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

      const [vocabData, progData] = await Promise.all([
        fetch("/api/v1/vocab", { headers }).then(r => r.json()),
        fetch("/api/v1/progress", { headers }).then(r => r.json()),
      ]);
      renderVocab(vocabData.entries || []);
      renderProgress(progData.progress || []);
    } catch (e) {
      localStorage.clear();
      location.href = "/auth/login";
    }
  }

  function renderVocab(entries) {
    const sec = document.createElement("section");
    sec.innerHTML = `<h2 class="me-h2">My vocabulary · ${entries.length} words</h2>`;
    if (!entries.length) {
      sec.innerHTML += `<p class="me-empty">No words yet. Open an article and click any
        out-of-scope word, then choose "Add to my words".</p>`;
    } else {
      const list = document.createElement("div");
      list.className = "vocab-list";
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
      sec.appendChild(list);
      sec.querySelectorAll(".vocab-remove").forEach(b => {
        b.addEventListener("click", async () => {
          const id = b.dataset.id;
          await fetch("/api/v1/vocab/" + id, {
            method: "DELETE", headers,
          });
          b.closest(".vocab-card").remove();
        });
      });
    }
    document.getElementById("me-content").appendChild(sec);
  }

  function renderProgress(rows) {
    const sec = document.createElement("section");
    sec.innerHTML = `<h2 class="me-h2">Reading progress · ${rows.length} articles</h2>`;
    if (!rows.length) {
      sec.innerHTML += `<p class="me-empty">No reading data yet.</p>`;
    } else {
      const list = document.createElement("div");
      list.className = "progress-list";
      rows.sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
      rows.forEach(p => {
        const item = document.createElement("a");
        item.className = "progress-row";
        const url = `/article-${p.issue_key}-${p.article_id}.html`
                    .replace("--", "-");
        item.href = `/article/${p.issue_key}/${p.article_id}`;
        const mins = Math.round((p.seconds_read || 0) / 60);
        item.innerHTML = `
          <span class="progress-issue">${escape(p.issue_key)}</span>
          <span class="progress-pct">${Math.round(p.scroll_pct || 0)}%</span>
          <span class="progress-time">${mins} min</span>
          <span class="progress-state">${p.completed ? "Completed" : "In progress"}</span>
        `;
        list.appendChild(item);
      });
      sec.appendChild(list);
    }
    document.getElementById("me-content").appendChild(sec);
  }

  function escape(s) {
    return String(s || "").replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;",
      '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  load();
})();