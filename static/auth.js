/* auth.js — 登录 / 注册
   保存 token 到 localStorage,跳转回主页。*/
(() => {
  const form = document.getElementById("auth-form");
  const help = document.getElementById("auth-help");
  if (!form) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    help.textContent = "";
    const fd = new FormData(form);
    const body = {
      email: fd.get("email"),
      password: fd.get("password"),
    };
    const mode = document.body.dataset.mode;
    const url = mode === "register" ? "/auth/register" : "/auth/login";
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) {
        help.textContent = data.detail || "Failed.";
        return;
      }
      localStorage.setItem("we_token", data.access_token);
      localStorage.setItem("we_user", JSON.stringify(data.user));
      location.href = "/me";
    } catch (err) {
      help.textContent = "Network error. Try again.";
    }
  });
})();