// KDS: poll open tickets, render FIFO board, advance state with one tap.
(function () {
  const POLL_MS = parseInt(document.body.dataset.pollMs || "4000", 10);
  const board = document.getElementById("board"), statusEl = document.getElementById("status");

  async function advance(order, next) {
    await fetch("/pos/api/orders/" + order.id + "/status/", {
      method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": window.CSRF},
      body: JSON.stringify({status: next})});
    poll(true);
  }

  function card(o, target) {
    const t = document.createElement("div");
    t.className = "t " + o.status + (o.elapsed_seconds > target ? " late" : "");
    const h = document.createElement("h3");
    const mins = Math.floor(o.elapsed_seconds / 60);
    h.textContent = "#" + o.order_number + " · " + o.order_type.replace("_", "-") + " · " + mins + " min";
    t.appendChild(h);
    const ul = document.createElement("ul");
    o.items.forEach(i => {
      const li = document.createElement("li"); li.textContent = i.quantity + " × " + i.name;
      if (i.notes) { const n = document.createElement("div"); n.className = "note"; n.textContent = i.notes; li.appendChild(n); }
      ul.appendChild(li);
    });
    t.appendChild(ul);
    const b = document.createElement("button");
    const next = o.status === "pending" ? "preparing" : "ready_to_serve";
    b.textContent = o.status === "pending" ? "Start preparing" : "Bump → Ready";
    b.onclick = () => advance(o, next);
    t.appendChild(b);
    return t;
  }

  async function poll(once) {
    try {
      const r = await fetch("/pos/api/kds/tickets/", {credentials: "same-origin"});
      const d = await r.json();
      board.replaceChildren(...d.tickets.map(o => card(o, d.target_seconds)));
      statusEl.textContent = d.tickets.length + " open · updated " + new Date().toLocaleTimeString();
    } catch (e) { statusEl.textContent = "offline – retrying"; }
    if (!once) setTimeout(poll, POLL_MS);
  }
  poll();
})();
