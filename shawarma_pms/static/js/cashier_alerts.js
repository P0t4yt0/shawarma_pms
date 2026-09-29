// PHASE 1: poll the ready-order feed every 3-5 s and alert the cashier once per event.
(function () {
  const POLL_MS = parseInt(document.body.dataset.pollMs || "4000", 10);
  const box = document.getElementById("alerts");
  let cursor = 0, firstLoad = true, timer = null;
  const shown = new Set();

  function beep() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const o = ctx.createOscillator(); o.frequency.value = 880; o.connect(ctx.destination);
      o.start(); setTimeout(() => { o.stop(); ctx.close(); }, 250);
    } catch (e) { /* audio blocked until first click */ }
  }

  function render(order) {
    if (shown.has(order.id)) return;
    shown.add(order.id);
    const el = document.createElement("div"); el.className = "alert";
    el.textContent = "Order #" + order.order_number + " is READY" +
      (order.customer_name ? " (" + order.customer_name + ")" : "");
    const btn = document.createElement("button"); btn.textContent = "Handed over";
    btn.onclick = async () => {
      const r = await fetch("/pos/api/orders/" + order.id + "/status/", {
        method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": window.CSRF},
        body: JSON.stringify({status: "completed"})});
      if (r.ok) el.remove();
    };
    el.appendChild(btn); box.appendChild(el);
  }

  async function poll() {
    try {
      const res = await fetch("/pos/api/orders/ready/?after=" + cursor, {credentials: "same-origin"});
      if (res.status === 403 || res.status === 401) return;          // stop polling if not allowed
      const data = await res.json();
      data.ready.forEach(render);                                     // authoritative state
      if (!firstLoad && data.events.length) beep();                   // one beep per NEW ready event
      cursor = data.cursor; firstLoad = false;
      // drop alerts that were completed elsewhere
      const readyIds = new Set(data.ready.map(o => o.id));
      shown.forEach(id => { if (!readyIds.has(id)) shown.delete(id); });
    } catch (e) { /* network blip: try again next tick */ }
    timer = setTimeout(poll, POLL_MS);
  }
  document.addEventListener("visibilitychange", () => {               // don't hammer the server from hidden tabs
    if (document.hidden) clearTimeout(timer); else poll();
  });
  poll();
  // PHASE 2: replace poll() with `new WebSocket(...)`; onmessage -> handle(event.order) using the SAME payload.
})();
