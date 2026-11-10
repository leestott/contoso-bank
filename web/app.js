"use strict";

const $ = (id) => document.getElementById(id);
const money = (pence) => new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP" }).format(pence / 100);
const labels = {
  ready: "Within autonomy", approval_required: "Approval required", approved: "Human approved",
  blocked: "Blocked by policy", purchased: "Purchased · simulated", reversed: "Reversed · simulated", rejected: "Rejected",
};
const tones = { ready: "good", approval_required: "warning", approved: "good", blocked: "bad", purchased: "good", reversed: "neutral", rejected: "neutral" };
let state;
let selectedId;
let busy = false;

function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}
function button(text, className, action) {
  const result = node("button", className, text);
  result.type = "button";
  result.disabled = busy;
  result.addEventListener("click", action);
  return result;
}
function badge(status) {
  return node("span", `status ${tones[status] || "neutral"}`, labels[status] || status);
}
function announce(message, error = false) {
  $("notice").textContent = message;
  $("notice").className = error ? "error" : "";
  $("notice").hidden = false;
  if (error) $("notice").scrollIntoView({ block: "nearest", behavior: "smooth" });
}
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Baku-Token": state.csrf_token },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}
async function refresh() {
  state = await api("/api/state");
  render();
}
async function operate(task, message) {
  if (busy) return;
  busy = true;
  document.querySelectorAll("button").forEach((el) => { el.disabled = true; });
  $("recommend").textContent = "Evaluating…";
  $("notice").hidden = true;
  try {
    const result = await task();
    if (result.order) selectedId = result.order.id;
    await refresh();
    announce(message);
  } catch (error) {
    announce(error.message, true);
  } finally {
    busy = false;
    document.querySelectorAll("button").forEach((el) => { el.disabled = false; });
    $("recommend").textContent = "Find & evaluate";
  }
}
function renderCatalog() {
  const body = $("catalog-body");
  body.replaceChildren();
  if (!state) return;
  state.catalog.filter((p) => p.category === $("category").value).forEach((p) => {
    const row = node("tr");
    const name = node("td");
    name.append(node("strong", "", p.name), node("small", "", p.merchant), node("small", "", p.detail));
    if (!state.policy.merchants.includes(p.merchant)) name.append(node("span", "status bad", "Merchant not allowed"));
    const action = node("td");
    action.append(button("Evaluate", "secondary", () => {
      if (!$("request-form").reportValidity()) return;
      operate(() => api("/api/catalog-order", { product_id: p.id, quantity: Number($("quantity").value) }), "Catalog item evaluated against current policy.");
    }));
    row.append(name, node("td", "price", money(p.price)), action);
    body.append(row);
  });
}
function renderDecision() {
  const order = state.orders.find((o) => o.id === selectedId);
  $("decision-empty").hidden = Boolean(order);
  $("decision").hidden = !order;
  $("decision").replaceChildren();
  $("decision-status").textContent = order ? labels[order.status] : "Awaiting request";
  $("decision-status").className = `status ${order ? tones[order.status] : "neutral"}`;
  if (!order) return;
  const panel = $("decision");
  panel.append(node("h3", "decision-product", order.product.name));
  panel.append(node("p", "muted", `${order.product.merchant} · ${order.quantity} × ${money(order.product.price)}`));
  const total = node("div", "decision-total");
  total.append(node("span", "muted", "Order total · GBP"), node("strong", "", money(order.total)));
  panel.append(total);
  const rationale = node("div", "rationale");
  rationale.append(node("h3", "", "Why this item?"), node("p", "", order.reason), node("span", "identity", order.source));
  if (order.response_id) rationale.append(node("div", "identity", `Foundry response: ${order.response_id}`));
  panel.append(rationale);
  const checks = node("ul", "checks");
  order.checks.forEach((c) => {
    const item = node("li");
    const content = node("div");
    content.append(node("strong", "", c.name), node("small", "", c.detail));
    item.append(node("span", `check-icon${c.pass ? "" : " fail"}`, c.pass ? "✓" : "×"), content);
    checks.append(item);
  });
  panel.append(checks, node("p", "field-help", "Checks shown at last evaluation. Current policy and available budget are checked again before purchase."));
  if (order.status === "approval_required") {
    const label = node("label", "check-label");
    const ack = node("input");
    ack.type = "checkbox";
    ack.id = "approval-ack";
    label.append(ack, document.createTextNode("As demo approver, I acknowledge this simulated purchase."));
    panel.append(label);
  }
  const actions = node("div", "decision-actions");
  const perform = (action, text) => operate(
    () => api(`/api/orders/${order.id}/${action}`, { acknowledged: $("approval-ack")?.checked === true }), text
  );
  if (order.status === "approval_required") actions.append(button("Approve request", "primary", () => perform("approve", "Human approval recorded. You can now simulate the purchase.")));
  if (["ready", "approved"].includes(order.status)) actions.append(button("Simulate purchase", "primary", () => perform("purchase", "Simulated purchase completed. No real money moved.")));
  if (order.status === "purchased") actions.append(button("Reverse purchase", "secondary", () => perform("reverse", "Simulated refund recorded and the original day's budget restored.")));
  if (["ready", "approved", "approval_required"].includes(order.status)) actions.append(button("Reject", "danger", () => perform("reject", "Request rejected. No purchase took place.")));
  panel.append(actions);
  if (order.approver) panel.append(node("p", "identity", `Approved by ${order.approver}. Demo role only; not a separate authenticated identity.`));
}
function renderOrders() {
  const body = $("orders-body");
  body.replaceChildren();
  $("order-count").textContent = `${state.orders.length} ${state.orders.length === 1 ? "request" : "requests"}`;
  if (!state.orders.length) {
    const cell = node("td", "empty-row", "No requests yet. Evaluate a purchase to start the ledger.");
    cell.colSpan = 6;
    const row = node("tr"); row.append(cell); body.append(row);
  }
  state.orders.forEach((order) => {
    const row = node("tr", order.id === selectedId ? "selected-row" : "");
    const product = node("td");
    product.append(node("strong", "", order.product.name), node("small", "", `${order.quantity} item(s) · ${order.product.merchant}`));
    const status = node("td"); status.append(badge(order.status));
    const action = node("td");
    action.append(button("Inspect", "secondary", () => {
      selectedId = order.id; renderOrders(); renderDecision();
      $("decision-title").scrollIntoView({ block: "nearest", behavior: "smooth" });
    }));
    row.append(node("td", "order-ref", order.id), product, node("td", "price", money(order.total)), status, node("td", "muted", order.source), action);
    body.append(row);
  });
}
function renderAudit() {
  $("audit-integrity").textContent = state.audit_valid ? "Hash chain verified" : "Audit integrity failure";
  $("audit-integrity").className = `status ${state.audit_valid ? "good" : "bad"}`;
  const list = $("audit-list");
  list.replaceChildren();
  if (!state.events.length) list.append(node("li", "muted", "The first action will start the audit trail."));
  [...state.events].reverse().slice(0, 30).forEach((event) => {
    const item = node("li");
    const time = node("time", "", new Date(event.timestamp).toLocaleTimeString("en-GB"));
    time.dateTime = event.timestamp;
    time.title = event.timestamp;
    const details = node("span", "audit-detail");
    let detail;
    if (typeof event.detail === "string") {
      detail = event.detail;
    } else if (Array.isArray(event.detail)) {
      detail = event.detail.map((check) => `${check.name}: ${check.pass ? "pass" : "not satisfied"}`).join(" · ");
    } else if (event.kind === "policy_updated") {
      detail = `Autonomous ${money(event.detail.auto_limit)} · transaction ${money(event.detail.transaction_limit)} · daily ${money(event.detail.daily_limit)}`;
    } else {
      detail = `${labels[event.detail.status] || event.detail.status} · ${money(event.detail.total)}`;
    }
    details.textContent = `${event.actor}${event.order_id ? ` · ${event.order_id}` : ""} · ${detail}`;
    item.append(time, node("strong", "", event.kind.replaceAll("_", " ")), details, node("code", "", `${event.hash.slice(0, 10)}…`));
    list.append(item);
  });
}
function renderPolicy() {
  $("auto-limit").value = (state.policy.auto_limit / 100).toFixed(2);
  $("transaction-limit").value = (state.policy.transaction_limit / 100).toFixed(2);
  $("daily-limit").value = (state.policy.daily_limit / 100).toFixed(2);
  $("always-approve").checked = state.policy.always_approve;
  $("merchant-options").replaceChildren();
  [...new Set(state.catalog.map((p) => p.merchant))].forEach((merchant) => {
    const label = node("label", "check-label");
    const input = node("input");
    input.type = "checkbox";
    input.value = merchant;
    input.checked = state.policy.merchants.includes(merchant);
    label.append(input, document.createTextNode(merchant));
    $("merchant-options").append(label);
  });
}
function render() {
  $("spent").textContent = money(state.spent);
  $("remaining").textContent = `${money(state.policy.daily_limit - state.spent)} remaining of ${money(state.policy.daily_limit)}`;
  $("project").textContent = state.foundry.project;
  $("survey-source").hidden = !state.survey;
  if (state.survey) $("survey-source").href = state.survey;
  renderCatalog(); renderDecision(); renderOrders(); renderPolicy(); renderAudit();
}
$("request-form").addEventListener("submit", (event) => {
  event.preventDefault();
  operate(() => api("/api/recommend", {
    request: $("request").value, category: $("category").value,
    quantity: Number($("quantity").value), mode: $("mode").value,
  }), "Recommendation recorded. Inspect the policy verdict before taking action.");
});
$("policy-form").addEventListener("submit", (event) => {
  event.preventDefault();
  operate(() => api("/api/policy", {
    auto_limit: Math.round(Number($("auto-limit").value) * 100),
    transaction_limit: Math.round(Number($("transaction-limit").value) * 100),
    daily_limit: Math.round(Number($("daily-limit").value) * 100),
    always_approve: $("always-approve").checked,
    merchants: [...$("merchant-options").querySelectorAll("input:checked")].map((el) => el.value),
  }), "Policy saved. Existing requests will be checked against it before purchase.");
});
$("category").addEventListener("change", renderCatalog);
$("mode").addEventListener("change", () => {
  $("engine-description").textContent = $("mode").value === "rules" ? "Deterministic catalog selection. No Azure call." : "Agent suggests. Policy engine authorises.";
});
document.querySelectorAll("[data-scenario]").forEach((element) => {
  element.addEventListener("click", () => {
    const scenario = element.dataset.scenario;
    if (scenario === "blocked") {
      $("category").value = "headsets";
      $("quantity").value = "1";
      renderCatalog();
      operate(() => api("/api/catalog-order", { product_id: "hs-29", quantity: 1 }), "Unapproved merchant blocked, even though its price is lower.");
      return;
    }
    $("category").value = scenario;
    $("quantity").value = "1";
    $("request").value = scenario === "monitors"
      ? "Find a 27-inch monitor for a new starter. Compare the approved suppliers."
      : "Find a good-value USB headset for our new starter.";
    renderCatalog();
    $("request").focus();
    announce("Scenario loaded. Select Find & evaluate to run it with the current policy.");
  });
});
refresh().catch((error) => {
  document.querySelectorAll("button").forEach((el) => { el.disabled = true; });
  announce(`Could not load the sandbox: ${error.message}. Reload after checking the server.`, true);
});
