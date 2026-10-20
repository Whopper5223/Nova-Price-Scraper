const chatEl = document.getElementById("chat");
const statusEl = document.getElementById("status");
const composer = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("sendBtn");
const confirmBox = document.getElementById("confirmBox");
const confirmList = document.getElementById("confirmList");
const confirmYes = document.getElementById("confirmYes");
const confirmNo = document.getElementById("confirmNo");
const resultsEl = document.getElementById("results");

let pendingItems = [];

// Item/store/product names and LLM-generated text are untrusted (user-typed or
// scraped) — always pass through here before it lands in an innerHTML template.
function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = String(value ?? "");
  return div.innerHTML;
}

// Minimal, safe markdown rendering for model replies. Escapes the raw text
// FIRST (same rule as every other untrusted string in this file), then
// applies only the two markdown features the model actually uses -- bold and
// bullet lists -- on top of the already-escaped string. Never treats raw
// text as HTML.
function renderMarkdown(text) {
  const escaped = escapeHtml(text);
  const withBold = escaped.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

  const htmlLines = [];
  let inList = false;
  for (const line of withBold.split("\n")) {
    const bullet = line.match(/^-\s+(.*)/);
    if (bullet) {
      if (!inList) {
        htmlLines.push("<ul>");
        inList = true;
      }
      htmlLines.push(`<li>${bullet[1]}</li>`);
      continue;
    }
    if (inList) {
      htmlLines.push("</ul>");
      inList = false;
    }
    if (line.trim()) htmlLines.push(`<p>${line}</p>`);
  }
  if (inList) htmlLines.push("</ul>");
  return htmlLines.join("");
}

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg msg--${role}`;
  div.innerHTML = renderMarkdown(text);
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
  return div;
}

function setBusy(busy) {
  input.disabled = busy;
  sendBtn.disabled = busy;
  sendBtn.textContent = busy ? "Thinking…" : "Send";
}

async function loadStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    statusEl.textContent = `${data.model} · ${data.prices}`;
  } catch {
    statusEl.textContent = "Could not reach the server.";
  }
}

function snapTag(eligible) {
  if (eligible === true) return `<span class="snap-tag snap-tag--eligible">SNAP eligible</span>`;
  if (eligible === false) return `<span class="snap-tag snap-tag--ineligible">not SNAP eligible</span>`;
  return `<span class="snap-tag snap-tag--unclear">verify at checkout</span>`;
}

function renderConfirm(items) {
  pendingItems = [...items];
  confirmBox.hidden = false;
  renderPills();
  confirmBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderPills() {
  confirmList.innerHTML = "";
  pendingItems.forEach((item, index) => {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = item;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "pill-remove";
    remove.setAttribute("aria-label", `Remove ${item}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      pendingItems.splice(index, 1);
      renderPills();
    });
    li.appendChild(label);
    li.appendChild(remove);
    confirmList.appendChild(li);
  });
  confirmYes.disabled = pendingItems.length === 0;
  confirmYes.textContent = pendingItems.length
    ? "Looks good — price it"
    : "Nothing left to price";
}

function renderStoreTotals(storeTotals, budget) {
  if (!storeTotals || !storeTotals.length) return "";

  // storeTotals is already sorted one-stop-coverage-first, then cheapest
  // first, so the top 2 rows ARE the two cheapest options as-is.
  const oneStop = storeTotals.find((s) => s.covers_all_items);
  const rankLabels = ["cheapest", "2nd cheapest"];
  const rows = storeTotals.map((s, index) => {
    const rank = index < rankLabels.length ? rankLabels[index] : null;
    const coverage = s.covers_all_items
      ? "has everything"
      : `missing ${escapeHtml(s.missing_items.join(", "))}`;
    return `
      <div class="store-row${rank ? " store-row--best" : ""}">
        <div>
          <div class="item-name">${escapeHtml(s.store_name)}${rank ? ` <span class="snap-tag snap-tag--eligible">${rank}</span>` : ""}</div>
          <div class="item-store">${coverage}</div>
        </div>
        <div class="item-price">$${s.total.toFixed(2)}</div>
      </div>`;
  }).join("");

  const budgetHtml = budget != null && oneStop
    ? (oneStop.total <= budget
        ? `<p class="missing">Within your $${budget.toFixed(2)} budget (${(budget - oneStop.total).toFixed(2)} to spare).</p>`
        : `<p class="missing">Over your $${budget.toFixed(2)} budget by $${(oneStop.total - budget).toFixed(2)} at the cheapest one-stop store.</p>`)
    : "";

  return `
    <h2 style="margin-top: var(--space-4)">Store comparison</h2>
    ${rows}
    ${budgetHtml}
  `;
}

function renderResults(payload) {
  const { findings, missing, summary, live_error, store_totals, budget } = payload;
  const items = Object.entries(findings);

  const rows = items.map(([item, rows]) => {
    const best = rows[0];
    const estimate = best.estimated_real_price != null
      ? `<div class="item-store">~$${best.estimated_real_price.toFixed(2)} estimated in-store price</div>`
      : "";
    return `
      <div class="item-row">
        <div>
          <div class="item-name">${escapeHtml(item)}</div>
          <div class="item-store">${escapeHtml(best.store_name)} — ${escapeHtml(best.name)}</div>
          ${estimate}
        </div>
        <div class="item-price">$${best.price.toFixed(2)}</div>
      </div>`;
  }).join("");

  const missingHtml = missing.length
    ? `<p class="missing">No price found for: ${escapeHtml(missing.join(", "))}</p>`
    : "";

  const errorHtml = live_error
    ? `<p class="missing">${escapeHtml(live_error)}</p>`
    : "";

  resultsEl.innerHTML = `
    <h2>Prices</h2>
    ${items.length ? rows : '<p class="missing">No prices found for any item.</p>'}
    ${missingHtml}
    ${errorHtml}
    ${renderStoreTotals(store_totals, budget)}
    <div class="summary">${escapeHtml(summary)}</div>
  `;
  resultsEl.hidden = false;
  resultsEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

composer.addEventListener("submit", async (e) => {
  e.preventDefault();
  const message = input.value.trim();
  if (!message) return;

  addMessage("user", message);
  input.value = "";
  confirmBox.hidden = true;
  resultsEl.hidden = true;
  setBusy(true);

  const thinking = addMessage("thinking", "Nova is thinking…");

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    const data = await res.json();
    thinking.remove();

    if (!res.ok) {
      addMessage("error", data.error || "Something went wrong talking to Ollama.");
      return;
    }

    addMessage("assistant", data.reply);
    if (data.ready && data.items.length) {
      renderConfirm(data.items);
    }
  } catch (err) {
    thinking.remove();
    addMessage("error", `Could not reach the server: ${err.message}`);
  } finally {
    setBusy(false);
    input.focus();
  }
});

confirmNo.addEventListener("click", () => {
  confirmBox.hidden = true;
  addMessage("assistant", "Okay — tell me more and I'll update the list.");
});

confirmYes.addEventListener("click", async () => {
  confirmBox.hidden = true;
  setBusy(true);
  const thinking = addMessage("thinking", "Looking up real prices — this can take a moment…");

  try {
    const res = await fetch("/api/price", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: pendingItems }),
    });
    const data = await res.json();
    thinking.remove();

    if (!res.ok) {
      addMessage("error", data.error || "Pricing failed.");
      return;
    }
    renderResults(data);
    addMessage("assistant", "Anything else you need?");
  } catch (err) {
    thinking.remove();
    addMessage("error", `Could not reach the server: ${err.message}`);
  } finally {
    setBusy(false);
    input.focus();
  }
});

// Page load shows a blank chat either way (no history is restored visually),
// so make that honest: reset the server-side session to match what's on
// screen. Without this, a reload LOOKS like a fresh start but secretly isn't
// -- the old conversation and budget silently carry over. Revisit once
// there's a real "new chat" button that resets deliberately instead of
// every reload.
fetch("/api/reset", { method: "POST" }).catch(() => {});
loadStatus();
input.focus();
