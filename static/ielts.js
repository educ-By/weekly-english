/* 雅思专区交互 —— 词表发音/收藏、闪卡自测、AI 写作批改与口语模拟。 */
(function () {
  "use strict";
  const page = document.body.dataset.page;
  const token = localStorage.getItem("we_token");
  const authHeaders = token ? { "Authorization": "Bearer " + token } : {};

  function needLogin() {
    if (!confirm("收藏与练习需要登录。现在去登录?")) return false;
    location.href = "/auth/login";
    return false;
  }

  function say(word) {
    try {
      const u = new SpeechSynthesisUtterance(word);
      u.lang = "en-GB";
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
    } catch (e) { /* 无 TTS 时静默 */ }
  }

  async function postJSON(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders },
      body: JSON.stringify(body),
    });
    if (r.status === 401) { needLogin(); return null; }
    return r.json();
  }

  // ---------------- 词表页 ----------------
  if (page === "ielts-vocab") {
    document.querySelectorAll(".ielts-word").forEach((li) => {
      const w = li.dataset.word;
      const trans = (li.querySelector(".ielts-trans") || {}).textContent || "";
      const btns = li.querySelectorAll(".ielts-btn");
      btns.forEach((b) => b.addEventListener("click", async () => {
        if (b.dataset.act === "say") { say(w); return; }
        if (b.dataset.act === "save") {
          if (!token) { needLogin(); return; }
          b.disabled = true;
          const out = await postJSON("/api/v1/vocab", {
            word: w, translation: trans.trim(), source_issue: "ielts",
          });
          b.textContent = out && out.ok ? "✓ 已收藏" : "重试";
          if (!(out && out.ok)) b.disabled = false;
        }
      }));
    });
  }

  // ---------------- 闪卡页 ----------------
  if (page === "ielts-flashcards") {
    const $ = (id) => document.getElementById(id);
    let deck = [];          // 待练队列
    let cur = null;
    let logged = false;
    let total = 0;
    let done = 0;
    const includeMastered = $("fc-include-mastered");

    function status(text) { $("fc-status").textContent = text; }

    function show() {
      if (!deck.length) {
        $("fc-card").classList.add("is-empty");
        $("fc-word").textContent = "没有待复习的词";
        $("fc-word").classList.add("fc-word-empty");
        status(done + " / " + total + " · 本轮完成 🎉");
        $("fc-progress").textContent = "";
        return;
      }
      cur = deck[0];
      done = total - deck.length + 1;
      $("fc-word").textContent = cur.word;
      $("fc-word-back").textContent = cur.word;
      $("fc-phon").textContent = cur.phonetic ? "/" + cur.phonetic + "/" : "";
      $("fc-pos").textContent = cur.pos || "";
      $("fc-trans").textContent = cur.translation || "(词典暂无释义)";
      $("fc-card").classList.remove("is-flipped");
      status((cur.box >= 3 ? "已掌握 · " : "") + "box " + cur.box + "/3");
      $("fc-progress").textContent = done + " / " + total;
    }

    function advance() {
      deck.shift();
      show();
    }

    async function judge(known) {
      if (!cur) return;
      if (token) {
        try {
          const out = await postJSON("/api/ielts/progress",
            { word: cur.word, known: known });
          if (out && typeof out.box === "number") cur.box = out.box;
        } catch (e) { /* 离线也不挡练习 */ }
      }
      if (known && cur.box >= 3 && !includeMastered.checked) {
        advance();          // 掌握且不复习 → 移出
      } else {
        deck.push(deck.shift());   // 排到队尾再来
      }
      show();
    }

    $("fc-card").addEventListener("click", (ev) => {
      if (ev.target.closest(".fc-say")) return;
      $("fc-card").classList.toggle("is-flipped");
    });
    $("fc-say").addEventListener("click", () => cur && say(cur.word));
    $("fc-known").addEventListener("click", () => judge(true));
    $("fc-unknown").addEventListener("click", () => judge(false));
    includeMastered.addEventListener("change", load);

    async function load() {
      status("Loading…");
      const all = includeMastered.checked
        ? ((await fetch("/api/ielts/drill?limit=60").then(r => r.json())).items || [])
        : await (async () => {
            const d = await fetch("/api/ielts/drill?limit=60").then(r => r.json());
            logged = !!d.logged_in;
            return (d.items || []).filter(w => w.box < 3);
          })();
      deck = all.slice();
      total = deck.length;
      show();
    }
    load();
  }

  // ---------------- AI 练习页 ----------------
  if (page === "ielts-practice") {
    // tabs
    document.querySelectorAll(".ielts-tab").forEach((t) => {
      t.addEventListener("click", () => {
        document.querySelectorAll(".ielts-tab").forEach(x => x.classList.remove("is-on"));
        document.querySelectorAll(".ielts-pane").forEach(x => x.classList.remove("is-on"));
        t.classList.add("is-on");
        document.getElementById("pane-" + t.dataset.tab).classList.add("is-on");
      });
    });

    const wrOut = document.getElementById("wr-output");
    const wrBtn = document.getElementById("wr-submit");
    wrBtn.addEventListener("click", async () => {
      const essay = document.getElementById("wr-essay").value.trim();
      if (!token) { needLogin(); return; }
      if (essay.length < 50) { alert("作文太短(至少 50 词)"); return; }
      wrBtn.disabled = true;
      wrOut.hidden = false;
      wrOut.textContent = "批改中…(整篇作文需要一点时间)";
      const out = await postJSON("/api/ielts/writing", {
        question: document.getElementById("wr-question").value,
        essay: essay,
      });
      wrBtn.disabled = false;
      wrOut.hidden = false;
      if (!out) return;
      wrOut.textContent = out.content || "AI 没有返回内容,请重试。";
    });

    // speaking
    const spLog = document.getElementById("sp-log");
    const spInput = document.getElementById("sp-input");
    const spStart = document.getElementById("sp-start");
    const spSend = document.getElementById("sp-send");
    const spEnd = document.getElementById("sp-end");
    let history = [];

    function addMsg(role, text) {
      const p = document.createElement("p");
      p.className = "sp-msg sp-" + role;
      const who = role === "examiner" ? "考官" : "你";
      p.innerHTML = "";
      p.textContent = who + ": " + text;
      spLog.appendChild(p);
      spLog.scrollTop = spLog.scrollHeight;
    }

    async function turn(action) {
      spStart.disabled = spSend.disabled = spEnd.disabled = true;
      const out = await postJSON("/api/ielts/speaking", {
        part: parseInt(document.getElementById("sp-part").value, 10),
        action: action,
        history: history,
      });
      spStart.disabled = spSend.disabled = spEnd.disabled = false;
      if (!out) return;
      const text = (out.content || "").trim();
      if (!text) { addMsg("examiner", "(AI 没有返回内容,请重试)"); return; }
      if (text.includes("===FEEDBACK===")) {
        addMsg("examiner", "— 考试结束,以下是评分反馈 —");
        addMsg("feedback", text.split("===FEEDBACK===")[1].trim());
        history = [];
        spStart.hidden = false; spSend.hidden = true; spEnd.hidden = true;
      } else {
        addMsg("examiner", text);
        history.push({ role: "examiner", text: text });
        spStart.hidden = true; spSend.hidden = false; spEnd.hidden = false;
      }
    }

    spStart.addEventListener("click", async () => {
      if (!token) { needLogin(); return; }
      history = [];
      spLog.innerHTML = "";
      addMsg("examiner", "(模拟开始)");
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
      await turn("feedback");
    });
  }
})();
