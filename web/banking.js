"use strict";
const $ = id => document.getElementById(id);
let config, session, state, pending = false, voice;
const scenes = {
  leyla: {
    title: "Recover a lost card",
    description: "Secure the card, check the last three transactions, and arrange a registered-address replacement and simulated virtual card.",
    prompts: {
      "en-GB": ["I've lost my debit card. Can you help me secure it?", "Read my last three transactions, then help me get a replacement and a virtual card."],
      "ru-RU": ["Я потеряла дебетовую карту. Помогите её заблокировать.", "Покажите три последние операции и помогите заказать новую и виртуальную карту."],
      "az-AZ": ["Debet kartımı itirmişəm. Onu bloklamağa kömək edin.", "Son üç əməliyyatı yoxlayın, yeni kart və virtual kart almağa kömək edin."]
    }
  },
  dmitri: {
    title: "Explore working capital",
    description: "Discuss a 50,000 AZN bakery loan, review twelve statements, and route the calculated memo to Rashad. Only the authorized human reviewer can decide.",
    prompts: {
      "en-GB": ["I run three bakeries and need 50,000 AZN for new ovens. Can you assess the application?", "Please explain the affordability calculation and send the memo for human review."],
      "ru-RU": ["У меня три пекарни. Нужен кредит 50 000 манатов на новые печи.", "Объясните расчёт платёжеспособности и передайте записку на рассмотрение человеку."],
      "az-AZ": ["Üç çörək sexim var. Yeni sobalar üçün 50 000 manat kredit lazımdır.", "Ödəmə qabiliyyəti hesablamasını izah edin və rəyi insan əməkdaşa göndərin."]
    }
  },
  james: {
    title: "Delegate payments, within limits",
    description: "Confirm the mandate, compare renewal cover, and pay insurance and electricity. Test the 650 AZN flight upgrade: the mandate must block it.",
    prompts: {
      "en-GB": ["Help me set up a payment mandate. Explain the limits before I confirm.", "Compare the insurance renewals and help me pay matching cover and electricity.", "Try the 650 AZN flight upgrade and explain why it is blocked."],
      "ru-RU": ["Помогите настроить платёжное поручение. Сначала объясните ограничения.", "Сравните продление страховки и помогите оплатить страховку и электричество.", "Попробуйте повышение класса за 650 манатов и объясните отказ."],
      "az-AZ": ["Ödəniş səlahiyyəti yaratmağa kömək edin. Təsdiqdən əvvəl limitləri izah edin.", "Sığorta yeniləmələrini müqayisə edin, sığorta və elektrik haqqını ödəməyə kömək edin.", "650 manatlıq uçuş yüksəltməsini sınayın və niyə bloklandığını izah edin."]
    }
  }
};
function notice(text, error = false) {
  $("notice").textContent = text;
  $("notice").classList.toggle("error", error);
  $("notice").hidden = false;
}
function showResult(operation, value) {
  $("result").textContent = JSON.stringify(value, null, 2);
  const summaries = {
    profile: "Caller profile retrieved.", verify_start: "Simulated OTP sent. Read the newest code from SMS-style app messages.",
    verify_complete: "Caller verified.", freeze_card: "Lost card frozen.", unfreeze_card: "Card unfrozen.",
    transactions: "Recent transactions retrieved.", replace_card: "Replacement arranged to the registered address.",
    virtual_card: "Simulated virtual card ready.", statements: "Synthetic monthly statements retrieved.",
    prepare_memo: "Credit memo prepared or retrieved. Only an authorized human can decide.",
    offers: "Server-owned insurance and bill options retrieved.", create_mandate: "Payment mandate confirmed.",
    pay: "Synthetic payment recorded.", one_off_request: "One-off review requested. The existing mandate has not changed.",
    handoff: "Human handoff requested. No human has joined this call yet.",
    indicative_credit_decision: "Human demo decision recorded. This is not a real credit offer."
  };
  const summary = value.error ? `Not completed: ${value.error}` : value.blocked ?
    `Blocked: ${value.reasons.join(", ")}. No funds charged.` :
    summaries[operation] || "Banking tool completed. Inspect the result for details.";
  $("action-summary").textContent = summary + (value.hosted_execution ?
    ` Hosted ${value.hosted_execution.role} worker and server policy checks recorded.` : "");
}
function selectedLocale() {
  return document.querySelector('input[name="caller-language"]:checked')?.value;
}
function selectLocale(locale) {
  document.querySelectorAll('input[name="caller-language"]').forEach(input => { input.checked = input.value === locale; });
}
function voiceProfile(locale) {
  if (!config.cloud) return;
  if (!locale) {
    $("deployment-status").textContent = "Choose English, Русский or Azərbaycan dili below to select the matching native voice.";
    return;
  }
  const version = config.voice_profiles?.[locale]?.version;
  $("deployment-status").textContent = version ?
    `Native ${locale} voice version ${version} is configured. Start a call to check the live connection.` :
    `Native ${locale} voice is currently unavailable. Banking controls remain available; no English voice is substituted.`;
  voice?.selectLocale(locale);
}
function guide() {
  const customer = config.customers.find(value => value.id === state.customer);
  const scene = scenes[state.customer];
  $("demo-name").textContent = customer.name;
  $("demo-dob").dateTime = customer.demo_dob;
  $("demo-dob").lang = state.locale;
  const date = new Date(`${customer.demo_dob}T12:00:00Z`);
  // Some browsers omit Azerbaijani month names from their Intl locale data.
  const azMonths = ["yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avqust", "sentyabr", "oktyabr", "noyabr", "dekabr"];
  $("demo-dob").textContent = state.locale === "az-AZ" ?
    `${date.getUTCDate()} ${azMonths[date.getUTCMonth()]} ${date.getUTCFullYear()}` :
    new Intl.DateTimeFormat(state.locale, {
      day: "numeric", month: "long", year: "numeric", timeZone: "UTC"
    }).format(date);
  $("scene-title").textContent = scene.title;
  $("scene-description").textContent = scene.description;
  $("mandate-guide").hidden = state.customer !== "james";
  const verified = ["Verify caller", state.verified];
  const steps = state.customer === "leyla" ?
    [verified, ["Freeze card", state.card === "frozen"], ["Replace card", Boolean(state.replacement)], ["Virtual card", state.virtual_card]] :
    state.customer === "dmitri" ?
      [verified, ["Prepare memo", Boolean(state.memo)], ["Human decision", Boolean(state.memo && state.memo.status !== "awaiting_human")]] :
      [verified, ["Confirm mandate", Boolean(state.mandate)],
        ["Pay insurance", state.payments.some(payment => payment.offer.startsWith("renewal"))],
        ["Pay electricity", state.payments.some(payment => payment.offer === "electricity")]];
  const current = steps.findIndex(([, done]) => !done);
  $("journey-progress").replaceChildren(...steps.map(([label, done], index) => {
    const item = document.createElement("li"); item.textContent = label;
    if (done) item.className = "done";
    else if (index === current) item.setAttribute("aria-current", "step");
    return item;
  }));
  const waitingOTP = !state.verified && Boolean(state.otp_expires);
  $("journey-next").textContent = waitingOTP ?
    "Next: read the current six-digit OTP from SMS-style app messages to the agent, or enter it in Verify caller." :
    current === -1 ? "Session milestones complete. Ask for a summary, inspect the tool evidence, or request a human." :
      state.customer === "dmitri" && current === 2 ?
        "Next: the authorized human reviewer must review the two-page memo and record an indicative decision." :
        state.customer === "james" && current === 1 ?
          "Next: review the mandate limits and use the synthetic mandate passcode shown in your caller guide, not the SMS verification code." :
          `Next: ${steps[current][0].toLowerCase()}. ${config.cloud ? "Use your voice or expand Manual banking controls below." : "Expand Manual banking controls below."}`;
  $("voice-prompts").replaceChildren(...scene.prompts[state.locale].map(text => {
    const button = document.createElement("button");
    button.type = "button"; button.className = "chip"; button.textContent = text; button.lang = state.locale;
    button.addEventListener("click", () => {
      $("voice-text").value = text; $("voice-text").focus();
      notice("Prompt drafted, not sent. Say it aloud on a microphone call, or connect text-only and choose Send to agent.");
    });
    return button;
  }));
}
async function bankingResponse(response, path) {
  if (response.type === "opaqueredirect" || response.redirected || response.status === 401) {
    throw new Error(`${path}: Microsoft sign-in is required. Refresh this page in your external browser and complete MFA.`);
  }
  const text = await response.text();
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    const reason = text.trim() ? "an invalid JSON response" : "an empty response";
    const recovery = response.status === 403 ?
      "The request was refused. Refresh the page and sign in again." :
      "The banking action is not confirmed. Refresh the page before continuing.";
    throw new Error(`${path}: ${reason} (HTTP ${response.status}). ${recovery}`);
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${path}: an invalid banking response (HTTP ${response.status}). No success is confirmed.`);
  }
  return value;
}
async function post(path, body) {
  const response = await fetch(`/api/bank/${path}`, {
    method: "POST", credentials: "same-origin", redirect: "manual",
    headers: {"Content-Type": "application/json", "X-Baku-Token": config.csrf_token},
    body: JSON.stringify({session, ...body})
  });
  const value = await bankingResponse(response, `/api/bank/${path}`);
  if (!response.ok || value.error) throw new Error(value.error || `HTTP ${response.status}`);
  return value;
}
async function refresh() {
  if (!session) return;
  state = await post("state", {});
  $("caller-name").textContent = `${state.name} · ${state.locale}`;
  $("verified").textContent = state.verified ? "Caller verified" : "DOB + OTP required";
  $("verified").className = `status ${state.verified ? "good" : "warning"}`;
  selectLocale(state.locale);
  voiceProfile(state.locale);
  guide();
  $("messages").replaceChildren(...state.messages.map(message => {
    const li = document.createElement("li");
    if (message.kind === "simulated-otp") {
      li.className = "otp-message";
      const code = document.createElement("strong"), help = document.createElement("span");
      code.textContent = message.text;
      help.textContent = "Simulated OTP · use the newest code within three minutes";
      li.append(code, help);
    } else li.textContent = `${message.kind}: ${message.text}`;
    return li;
  }));
  if (!state.messages.length) {
    const empty = document.createElement("li"); empty.className = "empty-message";
    empty.textContent = "No messages yet. Verify the synthetic DOB to receive your simulated OTP.";
    $("messages").append(empty);
  }
  $("handoff").textContent = state.handoff ? `${state.handoff.status} · ${state.handoff.team}` : "No handoff requested.";
  $("transcript").replaceChildren(...state.transcript.slice(-40).map(entry => {
    const li = document.createElement("li"), original = document.createElement("span"), english = document.createElement("span");
    original.lang = entry.locale;
    original.textContent = `${entry.speaker} · ${entry.locale}: ${entry.text}`;
    english.lang = "en-GB"; english.className = "field-help";
    english.textContent = entry.translation === "original" ? "" :
      entry.english ? `English (${entry.translation}): ${entry.english}` :
      entry.translation === "pending" ? "English translation pending…" : "English translation unavailable.";
    li.append(original, english);
    return li;
  }));
  if (!state.transcript.length) {
    const empty = document.createElement("li"); empty.className = "empty-message";
    empty.textContent = "No call transcript yet.";
    $("transcript").append(empty);
  }
  $("credit-review").hidden = !state.memo;
  if (state.memo) $("memo-summary").textContent =
    `${state.memo.id} · ${state.memo.status} · coverage ${state.memo.debt_service_coverage}. Not a binding offer.`;
  $("bank-state").textContent = JSON.stringify({
    verified: state.verified, card: state.card, replacement: state.replacement, virtual_card: state.virtual_card,
    mandate: state.mandate, payments: state.payments, one_off_requests: state.one_off_requests
  }, null, 2);
  const audit = await post("audit", {});
  $("audit-status").textContent = `${audit.events.length} actions · hash chain ${audit.chain_valid ? "verified" : "INVALID"} · ${config.cloud ? "hosted worker and authenticated human attribution" : "local operator attribution, not Entra agent identities"}`;
}
async function run(work) {
  if (pending) return;
  pending = true;
  $("customer").disabled = $("locale").disabled = true;
  $("session-workspace").setAttribute("aria-busy", "true");
  const buttons = [...document.querySelectorAll("main button")].filter(button => !button.closest("#voice-controls"));
  buttons.forEach(button => { button.disabled = true; });
  try {
    await work();
  } catch (error) {
    notice(error.message, true);
  } finally {
    try { await refresh(); } catch (error) { notice(error.message, true); }
    buttons.forEach(button => { button.disabled = config.cloud && config.reviewer === false && button.hasAttribute("data-decision"); });
    $("customer").disabled = $("locale").disabled = false;
    $("start").disabled = !selectedLocale();
    $("session-workspace").removeAttribute("aria-busy");
    pending = false;
  }
}
async function action(operation, payload = {}) {
  if (!session) throw new Error("Start a synthetic customer session first.");
  const value = await post("action", {operation, payload});
  showResult(operation, value);
  notice(value.blocked ? `Blocked: ${value.reasons.join(", ")}. No funds charged.` :
    `${operation.replaceAll("_", " ")} completed in the ${config.cloud ? "hosted-agent governed" : "local"} synthetic workflow.`, Boolean(value.blocked));
  return value;
}
$("start").addEventListener("click", () => run(async () => {
  voice?.end();
  const selected = config.customers.find(customer => customer.id === $("customer").value);
  if (!selectedLocale()) throw new Error("Choose your preferred language before starting a session.");
  const value = await post("start", {customer: selected.id, locale: selectedLocale()});
  session = value.session;
  $("voice-captions").replaceChildren();
  $("voice-empty").hidden = false;
  $("voice-text").value = "";
  $("session-workspace").hidden = false;
  ["leyla", "dmitri", "james"].forEach(id => { $(id).hidden = id !== selected.id; });
  $("dob").value = selected.demo_dob;
  $("otp").value = "";
  $("passcode").value = "";
  $("mandate-confirm").checked = false;
  $("review-confirm").checked = false;
  $("result").textContent = "New isolated synthetic session. Verify date of birth and simulated OTP.";
  $("action-summary").textContent = "No banking action yet. Start with caller verification.";
  notice(config.cloud ? `Session started for ${selected.name}. Start the microphone or explicitly connect text-only.` :
    `Session started for ${selected.name}. No live agent or microphone is connected.`);
}));
$("verify-form").addEventListener("submit", event => {
  event.preventDefault(); run(() => action("verify_start", {date_of_birth: $("dob").value}));
});
$("otp-form").addEventListener("submit", event => {
  event.preventDefault(); run(() => action("verify_complete", {otp: $("otp").value}));
});
$("memo-form").addEventListener("submit", event => {
  event.preventDefault(); run(() => action("prepare_memo", {amount: Number($("amount").value)}));
});
$("mandate-form").addEventListener("submit", event => {
  event.preventDefault(); run(() => action("create_mandate",
    {passcode: $("passcode").value, confirmed: $("mandate-confirm").checked}));
});
$("customer").addEventListener("change", () => {
  if (session) return notice("New journey selected. Choose Start demo to switch callers; your current call and banking session have not changed.");
  voiceProfile(selectedLocale());
});
$("locale").addEventListener("change", () => run(async () => {
  if (!session) {
    voiceProfile(selectedLocale());
    $("start").disabled = false;
    return;
  }
  voice?.end("Changing caller language. Microphone off.");
  await action("set_locale", {locale: selectedLocale()});
  notice("Caller language changed. Start a new voice call for this language; your banking session and verification are preserved.");
}));
document.querySelectorAll("[data-operation]").forEach(button =>
  button.addEventListener("click", () => run(() => action(button.dataset.operation))));
document.querySelectorAll("[data-offer]").forEach(button =>
  button.addEventListener("click", () => run(() => action("pay",
    {offer_id: button.dataset.offer, mandate_id: state?.mandate?.id}))));
$("one-off").addEventListener("click", () => run(() => action("one_off_request",
  {offer_id: "flight-upgrade", mandate_id: state?.mandate?.id})));
document.querySelectorAll("[data-decision]").forEach(button =>
  button.addEventListener("click", () => run(async () => {
    const value = await post("decision", {decision: button.dataset.decision, acknowledged: $("review-confirm").checked});
    showResult("indicative_credit_decision", value);
    notice("Human demo decision recorded. This is not a real credit offer.");
  })));
$("view-memo").addEventListener("click", () => {
  $("memo-pages").replaceChildren(...state.memo.pages.map(text => {
    const page = document.createElement("section"); page.className = "memo-page"; page.textContent = text; return page;
  }));
  $("memo-dialog").showModal();
});
$("close-memo").addEventListener("click", () => $("memo-dialog").close());
$("injection").addEventListener("click", () => run(async () => {
  if (!session) throw new Error("Start a session first.");
  const response = await fetch("/api/bank/action", {
    method: "POST", credentials: "same-origin", redirect: "manual",
    headers: {"Content-Type": "application/json", "X-Baku-Token": config.csrf_token},
    body: JSON.stringify({session, operation: "profile", payload: {customer_id: state.customer === "james" ? "leyla" : "james"}})
  });
  const value = await bankingResponse(response, "/api/bank/action");
  if (!response.ok || !value.flagged) throw new Error("Cross-customer refusal did not produce the expected flagged result.");
  $("result").textContent = JSON.stringify(value, null, 2);
  notice("Cross-customer request refused and flagged. This is a deterministic tool-boundary test, not a model safety evaluation.");
}));
$("export-audit").addEventListener("click", () => run(async () => {
  const value = await post("audit", {});
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type: "application/json"}));
  const link = document.createElement("a"); link.href = url; link.download = "baku-banking-audit.json";
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}));
(async () => {
  try {
    const response = await fetch("/api/bank/config", {credentials: "same-origin", redirect: "manual"});
    config = await bankingResponse(response, "/api/bank/config");
    if (!response.ok) throw new Error(`Could not load banking configuration: HTTP ${response.status}`);
    if (config.cloud) voice = window.BakuVoice({config, getSession: () => session, refresh, notice, showResult});
    $("customer").replaceChildren(...config.customers.map(customer => {
      const option = document.createElement("option"); option.value = customer.id;
      option.textContent = `${customer.name} · ${scenes[customer.id].title}`; return option;
    }));
    $("bank-project").textContent = config.project;
    $("bank-region").textContent = config.region;
    if (config.cloud) {
      $("identity-status").textContent = "Signed-in presenter · tenant-restricted access · human-only credit review.";
      $("experience-description").textContent = "Azerbaijani card support, Russian-to-English SME lending, and English agentic payments. Native Foundry voice and server-governed synthetic banking.";
      $("deployment-title").textContent = "Authenticated cloud banking workspace";
      document.querySelector(".deployment-warning").classList.add("cloud-configured");
      voiceProfile(selectedLocale());
      $("execution-description").textContent = "Banking actions invoke hosted orchestrator and specialist workers before server policy checks. Failed hosted calls do not fall back to local execution.";
      $("execution-mode").textContent = "HOSTED WORKERS + SERVER POLICY";
      $("staff-mode").textContent = config.reviewer ? "AUTHORIZED HUMAN REVIEWER" : "AUTHENTICATED PRESENTER";
      $("cloud-scope").textContent = "Native voice and hosted workers · synthetic banking · MCP toolbox, Foundry IQ and portal evaluations are separate acceptance gates.";
      document.querySelectorAll("[data-decision]").forEach(button => { button.disabled = !config.reviewer; });
      const nav = document.querySelector('nav a[href="/"]');
      if (nav) { nav.textContent = "Banking workspace"; nav.href = "/banking"; }
    } else {
      document.querySelector("#welcome-title + p").textContent =
        "Choose a language and journey, then start the demo. This local page uses manual banking controls; microphone calls require the authenticated cloud app.";
      $("manual-controls").open = true;
      $("deployment-status").textContent = config.voice_version ?
        `Saved voice version ${config.voice_version} is configured, but this manual workflow page has no microphone integration.` :
        `Last recorded project state: ${config.project_state}. No native voice-agent version is configured. This is saved deployment metadata, not a live Azure health check.`;
    }
    $("locale").disabled = false;
    $("start").disabled = !selectedLocale();
  } catch (error) { notice(error.message, true); $("start").disabled = true; }
})();
