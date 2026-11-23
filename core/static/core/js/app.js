// Shared helpers
window.csrf = () => document.cookie.split("; ").find(c => c.startsWith("csrftoken="))?.split("=")[1] || "";
window.api = async (url, body) => {
  const opt = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()}, body: JSON.stringify(body)};
  const r = await fetch(url, opt);
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(data.error || "Something went wrong.");
  return data;
};
window.taka = n => {
  const [w, d] = Math.abs(n).toFixed(0).split(".");
  const g = w.length > 3 ? w.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + w.slice(-3) : w;
  return (n < 0 ? "-" : "") + "৳" + g;
};
// Voice input (Web Speech API) - bn-BD, falls back to en-US
window.voice = (btn, input) => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { btn.style.display = "none"; return; }
  btn.addEventListener("click", () => {
    const r = new SR(); r.lang = "bn-BD"; r.interimResults = false;
    btn.classList.add("loading");
    r.onresult = e => { input.value = (input.value + " " + e.results[0][0].transcript).trim(); input.dispatchEvent(new Event("input")); };
    r.onend = r.onerror = () => btn.classList.remove("loading");
    r.start();
  });
};
