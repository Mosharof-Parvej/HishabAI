// Shared helpers
window.csrf = () => document.cookie.split("; ").find(c => c.startsWith("csrftoken="))?.split("=")[1] || "";
window.api = async (url, body) => {
  const opt = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()}, body: JSON.stringify(body)};
  const r = await fetch(url, opt);
  aiMeter(r.headers.get("X-AI-Used"));
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(data.error || "Something went wrong.");
  return data;
};
window.aiMeter = used => {  // refresh every "AI today" meter on the page
  if (used === null || used === undefined || used === "") return;
  document.querySelectorAll(".aimeter").forEach(m => {
    const limit = +m.dataset.limit || 1, pct = Math.min(100, Math.round(+used / limit * 100));
    m.querySelector("[data-ai-pct]").textContent = pct; m.querySelector("[data-ai-used]").textContent = used;
    const bar = m.querySelector(".bar"); bar.querySelector("i").style.width = pct + "%";
    bar.classList.toggle("danger", pct >= 90); bar.classList.toggle("warn", pct >= 70 && pct < 90);
  });
};
window.taka = n => {
  const [w] = Math.abs(n).toFixed(0).split(".");
  const g = w.length > 3 ? w.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," + w.slice(-3) : w;
  return (n < 0 ? "-" : "") + "৳" + g;
};

/* Voice input, ChatGPT style: tap the mic to start recording, tap again to stop. The audio is sent to the server
   (Whisper / Gemini) and the transcript is appended to the input. Works in every browser with a microphone,
   including Brave and Firefox, which do not ship the built-in Web Speech API. Esc cancels. */
window.voice = (btn, input) => {
  const IDLE = "🎤", REC = "⏹";
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    btn.disabled = true; btn.title = "Voice input isn't supported in this browser"; return;
  }
  const stat = document.createElement("span");
  stat.className = "vstat"; stat.hidden = true; stat.setAttribute("role", "status");
  btn.parentNode.insertBefore(stat, btn);
  btn.type = "button"; btn.textContent = IDLE; btn.title = "Speak (Bangla or English)";
  let state = "idle", rec, stream, chunks, timer, raf, ctx, t0, cancelled = false;

  const say = (msg, cls = "") => { stat.textContent = msg; stat.className = "vstat " + cls; stat.hidden = !msg; };
  const fmt = s => Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0");
  const cleanup = () => {
    clearInterval(timer); cancelAnimationFrame(raf); stream?.getTracks().forEach(t => t.stop()); ctx?.close().catch(() => {});
    btn.classList.remove("rec"); btn.style.removeProperty("--lvl"); btn.textContent = IDLE;
  };

  async function start() {
    cancelled = false; chunks = [];
    try { stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}}); }
    catch (e) {
      say(e.name === "NotFoundError" ? "No microphone found." : "Microphone is blocked. Allow it in your browser's site settings, then try again.", "err");
      setTimeout(() => say(""), 6000); return;
    }
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"].find(t => MediaRecorder.isTypeSupported(t));
    rec = new MediaRecorder(stream, mime ? {mimeType: mime} : {});
    rec.ondataavailable = e => e.data.size && chunks.push(e.data);
    rec.onstop = finish;
    rec.start(); t0 = Date.now(); state = "recording";
    btn.classList.add("rec"); btn.textContent = REC;
    say("● Listening… 0:00 · tap ⏹ to finish", "live");
    timer = setInterval(() => {
      const s = (Date.now() - t0) / 1000; say(`● Listening… ${fmt(s)} · tap ⏹ to finish`, "live");
      if (s >= 60) stop();
    }, 250);
    try {  // live level ring around the button
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      const an = ctx.createAnalyser(); an.fftSize = 256; ctx.createMediaStreamSource(stream).connect(an);
      const buf = new Uint8Array(an.fftSize);
      const tick = () => { an.getByteTimeDomainData(buf); let sum = 0; for (const v of buf) sum += ((v - 128) / 128) ** 2;
        btn.style.setProperty("--lvl", Math.min(1, Math.sqrt(sum / buf.length) * 5).toFixed(2)); raf = requestAnimationFrame(tick); };
      tick();
    } catch (e) {}
  }
  const stop = () => { if (state === "recording") { state = "busy"; rec.stop(); } };

  async function finish() {
    const secs = (Date.now() - t0) / 1000, type = rec.mimeType || "audio/webm";
    cleanup();
    if (cancelled) { state = "idle"; say(""); return; }
    if (secs < 0.7) { state = "idle"; say("Too short — hold on a little longer.", "err"); setTimeout(() => say(""), 3000); return; }
    btn.disabled = true; say("Transcribing…", "busy");
    try {
      const fd = new FormData(); fd.append("audio", new Blob(chunks, {type}), "voice");
      const r = await fetch("/api/transcribe/", {method: "POST", headers: {"X-CSRFToken": csrf()}, body: fd});
      aiMeter(r.headers.get("X-AI-Used"));
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || "Couldn't transcribe that.");
      input.value = (input.value.trim() ? input.value.replace(/\s+$/, "") + " " : "") + d.text;
      input.dispatchEvent(new Event("input", {bubbles: true})); input.focus(); say("");
    } catch (e) { say(e.message, "err"); setTimeout(() => say(""), 6000); }
    btn.disabled = false; state = "idle";
  }

  btn.addEventListener("click", () => { if (state === "idle") start(); else if (state === "recording") stop(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && state === "recording") { cancelled = true; stop(); } });
};
