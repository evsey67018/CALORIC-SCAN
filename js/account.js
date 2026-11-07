/* Аккаунт, лимит сканов и оплата. Распознавание еды живёт в app.js. */
const FREE_LIMIT = 3;
const QUOTA_KEY = "caloric-scan-quota-v1";
const SUPABASE_UMD = "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.47.10/dist/umd/supabase.js";

const account = {
  client: null,
  session: null,
  quota: { allowed: true, used: 0, limit: FREE_LIMIT, premium: false },
};

function cfg() {
  const raw = window.CALORIC_CONFIG || {};
  const url = String(raw.supabaseUrl || "").trim();
  const key = String(raw.supabaseAnonKey || "").trim();
  const pay = String(raw.paymentWorkerUrl || "").trim().replace(/\/$/, "");
  const placeholder = (value) => !value || value.includes("YOUR_");
  return {
    url,
    key,
    pay,
    supabaseReady: url.startsWith("https://") && !placeholder(url) && !placeholder(key),
    payReady: pay.startsWith("https://") && !placeholder(pay),
  };
}

function moscowDay(date = new Date()) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Moscow",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function dayTitle(day) {
  const [year, month, date] = day.split("-").map(Number);
  const utc = new Date(Date.UTC(year, month - 1, date, -3, 0, 0));
  return utc.toLocaleDateString("ru-RU", { day: "numeric", month: "long", timeZone: "Europe/Moscow" });
}

function localStatus() {
  let used = 0;
  try {
    const raw = JSON.parse(localStorage.getItem(QUOTA_KEY) || "{}");
    used = Number(raw[moscowDay()]) || 0;
  } catch { used = 0; }
  return { allowed: used < FREE_LIMIT, used, limit: FREE_LIMIT, premium: false, source: "local" };
}

function localConsume() {
  const status = localStatus();
  if (!status.allowed) return status;
  const raw = JSON.parse(localStorage.getItem(QUOTA_KEY) || "{}");
  raw[moscowDay()] = status.used + 1;
  localStorage.setItem(QUOTA_KEY, JSON.stringify(raw));
  return { allowed: true, used: status.used + 1, limit: FREE_LIMIT, premium: false, source: "local" };
}

function renderQuota(status) {
  account.quota = status;
  const node = document.getElementById("quotaText");
  if (!node || !status) return;
  if (status.premium) {
    const until = status.premium_until
      ? new Date(status.premium_until).toLocaleDateString("ru-RU", { day: "numeric", month: "long" })
      : "";
    node.textContent = until ? `Премиум до ${until} · сканы без лимита` : "Премиум · сканы без лимита";
    return;
  }
  const left = Math.max(0, status.limit - status.used);
  node.textContent = `Осталось ${left} из ${status.limit} сканов сегодня`;
}

function setAuthButton() {
  const button = document.getElementById("authBtn");
  const email = document.getElementById("authEmailLabel");
  if (!button) return;
  if (account.session?.user) {
    button.textContent = "Выйти";
    button.dataset.mode = "out";
    if (email) {
      email.hidden = false;
      email.textContent = account.session.user.email || "";
    }
  } else {
    button.textContent = "Войти";
    button.dataset.mode = "in";
    if (email) email.hidden = true;
  }
}

function showView(name) {
  const scan = document.getElementById("viewScan");
  const diary = document.getElementById("viewDiary");
  const scanBtn = document.getElementById("navScan");
  const diaryBtn = document.getElementById("navDiary");
  const diaryOn = name === "diary";
  if (scan) scan.hidden = diaryOn;
  if (diary) diary.hidden = !diaryOn;
  scanBtn?.setAttribute("aria-current", diaryOn ? "false" : "page");
  diaryBtn?.setAttribute("aria-current", diaryOn ? "page" : "false");
  if (diaryOn) refreshDiary();
}

async function loadSdk() {
  if (window.supabase?.createClient) return;
  await new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = SUPABASE_UMD;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("supabase sdk"));
    document.head.appendChild(script);
  });
}

async function refreshStatus() {
  const config = cfg();
  if (!config.supabaseReady || !account.client || !account.session) {
    renderQuota(localStatus());
    return;
  }
  const { data, error } = await account.client.rpc("scan_status");
  if (error) {
    console.warn(error);
    renderQuota(localStatus());
    return;
  }
  renderQuota({ ...data, source: "server" });
}

async function consumeScan() {
  const config = cfg();
  if (config.supabaseReady && account.client && account.session) {
    const { data, error } = await account.client.rpc("consume_scan");
    if (error) throw error;
    const status = { ...data, source: "server" };
    renderQuota(status);
    return status;
  }
  const status = localConsume();
  renderQuota(status);
  return status;
}

function openPaywall() {
  const note = document.getElementById("payNote");
  if (note) {
    note.hidden = true;
    note.textContent = "";
  }
  document.getElementById("paywall")?.showModal();
}

async function startPayment(plan) {
  const note = document.getElementById("payNote");
  const config = cfg();
  const show = (text) => {
    if (!note) return;
    note.hidden = false;
    note.textContent = text;
  };
  if (!config.supabaseReady || !account.session) {
    if (config.supabaseReady) {
      show("Сначала войди по почте — подписка привяжется к аккаунту.");
      document.getElementById("authDialog")?.showModal();
    } else {
      show("Оплата скоро будет доступна");
    }
    return;
  }
  if (!config.payReady) {
    show("Оплата скоро будет доступна");
    return;
  }
  show("Открываем оплату…");
  try {
    const response = await fetch(`${config.pay}/create-payment`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${account.session.access_token}`,
      },
      body: JSON.stringify({ plan, return_url: window.location.href }),
    });
    const body = await response.json().catch(() => ({}));
    if (response.status === 501 || body.error === "payments_not_configured") {
      show("Оплата скоро будет доступна");
      return;
    }
    if (!response.ok || !body.confirmation_url) {
      show("Не удалось создать платёж. Попробуй ещё раз позже.");
      return;
    }
    window.location.href = body.confirmation_url;
  } catch (error) {
    console.warn(error);
    show("Оплата скоро будет доступна");
  }
}

async function sendMagicLink(event) {
  event.preventDefault();
  const message = document.getElementById("authMsg");
  const email = document.getElementById("authEmail")?.value?.trim();
  const config = cfg();
  if (!config.supabaseReady || !account.client) {
    if (message) message.textContent = "Вход появится, когда будет подключён Supabase.";
    return;
  }
  if (!email) return;
  const { error } = await account.client.auth.signInWithOtp({
    email,
    options: { emailRedirectTo: window.location.origin + window.location.pathname },
  });
  if (message) {
    message.textContent = error
      ? "Не получилось отправить письмо. Проверь адрес."
      : "Письмо ушло. Открой ссылку или введи код из письма.";
  }
}

async function verifyCode() {
  const message = document.getElementById("authMsg");
  const email = document.getElementById("authEmail")?.value?.trim();
  const token = document.getElementById("authCode")?.value?.trim();
  if (!account.client || !email || !token) {
    if (message) message.textContent = "Нужны почта и код из письма.";
    return;
  }
  const { error } = await account.client.auth.verifyOtp({ email, token, type: "email" });
  if (error) {
    if (message) message.textContent = "Код не подошёл. Запроси письмо ещё раз.";
    return;
  }
  document.getElementById("authDialog")?.close();
}

async function signOut() {
  if (account.client) await account.client.auth.signOut();
  account.session = null;
  setAuthButton();
  renderQuota(localStatus());
  refreshDiary();
}

function mapRemote(row) {
  const created = row.created_at ? new Date(row.created_at) : new Date();
  return {
    id: "remote-" + row.id,
    nameRu: row.dish || "Тарелка",
    detail: "",
    kcal: Number(row.kcal) || 0,
    protein: Number(row.protein) || 0,
    fat: Number(row.fat) || 0,
    carbs: Number(row.carbs) || 0,
    grams: Number(row.grams) || 0,
    day: moscowDay(created),
    remoteId: row.id,
  };
}

function localItems() {
  const data = JSON.parse(localStorage.getItem("caloric-scan-diary-v1") || "{}");
  const items = [];
  for (const [day, rows] of Object.entries(data.days || {})) {
    for (const row of rows || []) items.push({ ...row, day });
  }
  return items;
}

let goalLoaded = false;

function fillDiary(items) {
  if (!goalLoaded) {
    goalLoaded = true;
    try {
      const saved = JSON.parse(localStorage.getItem("caloric-scan-diary-v1") || "{}");
      const input = document.getElementById("goal");
      if (input && Number(saved.goal) >= 800) input.value = String(saved.goal);
    } catch { /* цель останется 2000 */ }
  }
  const list = document.getElementById("diaryList");
  const empty = document.getElementById("diaryEmpty");
  const goalInput = document.getElementById("goal");
  const goalText = document.getElementById("goalText");
  const dayMacros = document.getElementById("dayMacros");
  const goalFill = document.getElementById("goalFill");
  const title = document.getElementById("diaryTitle");
  if (!list) return;
  const today = moscowDay();
  const goal = Number(goalInput?.value) || 2000;
  const todays = items.filter((item) => item.day === today);
  const totals = todays.reduce((acc, item) => {
    acc.kcal += Number(item.kcal) || 0;
    acc.protein += Number(item.protein) || 0;
    acc.fat += Number(item.fat) || 0;
    acc.carbs += Number(item.carbs) || 0;
    return acc;
  }, { kcal: 0, protein: 0, fat: 0, carbs: 0 });
  if (title) title.textContent = "Дневник · " + dayTitle(today);
  if (goalText) {
    goalText.textContent = totals.kcal <= goal
      ? `${Math.round(totals.kcal)} из ${goal} ккал · осталось ${Math.max(0, Math.round(goal - totals.kcal))}`
      : `${Math.round(totals.kcal)} из ${goal} ккал · цель превышена на ${Math.round(totals.kcal - goal)}`;
  }
  if (dayMacros) {
    dayMacros.textContent = todays.length
      ? `Белки ${totals.protein.toFixed(1)} г · Жиры ${totals.fat.toFixed(1)} г · Углеводы ${totals.carbs.toFixed(1)} г`
      : "";
  }
  if (goalFill) {
    goalFill.style.width = `${goal > 0 ? Math.min(100, (totals.kcal / goal) * 100) : 0}%`;
    goalFill.parentElement.classList.toggle("over", totals.kcal > goal);
  }
  if (empty) empty.hidden = items.length > 0;
  const byDay = new Map();
  for (const item of items) {
    if (!byDay.has(item.day)) byDay.set(item.day, []);
    byDay.get(item.day).push(item);
  }
  const days = [...byDay.keys()].sort().reverse();
  list.replaceChildren();
  for (const day of days) {
    const rows = byDay.get(day);
    const head = document.createElement("li");
    head.className = "day-head";
    const name = document.createElement("b");
    name.textContent = day === today ? "Сегодня" : dayTitle(day);
    const sum = document.createElement("small");
    sum.textContent = `${Math.round(rows.reduce((acc, row) => acc + (Number(row.kcal) || 0), 0))} ккал`;
    head.append(name, sum);
    list.appendChild(head);
    for (const item of rows) {
      const li = document.createElement("li");
      const text = document.createElement("div");
      const label = document.createElement("b");
      label.textContent = item.nameRu || "Тарелка";
      const meta = document.createElement("small");
      meta.textContent = (item.detail ? `${item.detail} · ` : "") + `${Math.round(item.kcal)} ккал`;
      text.append(label, meta);
      const del = document.createElement("button");
      del.type = "button";
      del.className = "del";
      del.setAttribute("aria-label", "Удалить запись");
      del.textContent = "✕";
      del.addEventListener("click", () => removeEntry(item));
      li.append(text, del);
      list.appendChild(li);
    }
  }
}

async function refreshDiary() {
  if (account.client && account.session) {
    const { data, error } = await account.client
      .from("scans")
      .select("id, created_at, dish, kcal, protein, fat, carbs, grams")
      .order("created_at", { ascending: false })
      .limit(300);
    if (!error && data) {
      fillDiary(data.map(mapRemote));
      return;
    }
  }
  fillDiary(localItems());
}

function mapRemote(row) {
  return {
    id: "remote-" + row.id,
    remoteId: row.id,
    nameRu: row.dish || "Тарелка",
    detail: "",
    kcal: Number(row.kcal) || 0,
    protein: Number(row.protein) || 0,
    fat: Number(row.fat) || 0,
    carbs: Number(row.carbs) || 0,
    grams: Number(row.grams) || 0,
    day: moscowDay(row.created_at ? new Date(row.created_at) : new Date()),
  };
}

async function removeEntry(item) {
  if (item.remoteId && account.client && account.session) {
    await account.client.from("scans").delete().eq("id", item.remoteId);
    await refreshDiary();
    return;
  }
  const data = JSON.parse(localStorage.getItem("caloric-scan-diary-v1") || "{}");
  if (data.days?.[item.day]) {
    data.days[item.day] = data.days[item.day].filter((row) => row.id !== item.id);
    localStorage.setItem("caloric-scan-diary-v1", JSON.stringify(data));
  }
  refreshDiary();
}

async function afterScan(meal) {
  if (!meal || meal.remoteSaved || !account.client || !account.session) return;
  const grams = meal.rows.reduce((sum, row) => sum + row.grams, 0);
  const dish = meal.dish?.ru || meal.rows.slice(0, 4).map((row) => row.ru).join(", ") || "Тарелка";
  const { error } = await account.client.from("scans").insert({
    user_id: account.session.user.id,
    dish,
    kcal: Math.round(meal.kcal),
    protein: Number(meal.protein.toFixed(1)),
    fat: Number(meal.fat.toFixed(1)),
    carbs: Number(meal.carbs.toFixed(1)),
    grams: Math.round(grams),
  });
  if (error) {
    console.warn(error);
    return;
  }
  meal.remoteSaved = true;
  const button = document.getElementById("addBtn");
  if (button) button.textContent = "В дневнике ✓";
  refreshDiary();
}

function wire() {
  document.getElementById("navScan")?.addEventListener("click", () => showView("scan"));
  document.getElementById("navDiary")?.addEventListener("click", () => showView("diary"));
  document.getElementById("authBtn")?.addEventListener("click", () => {
    if (account.session) signOut();
    else document.getElementById("authDialog")?.showModal();
  });
  document.getElementById("authForm")?.addEventListener("submit", sendMagicLink);
  document.getElementById("authVerify")?.addEventListener("click", verifyCode);
  document.getElementById("authClose")?.addEventListener("click", () => document.getElementById("authDialog")?.close());
  document.getElementById("payClose")?.addEventListener("click", () => document.getElementById("paywall")?.close());
  document.querySelectorAll("[data-plan]").forEach((button) => {
    button.addEventListener("click", () => startPayment(button.dataset.plan));
  });
  renderQuota(localStatus());
  const hint = document.getElementById("diaryHint");
  if (hint) hint.hidden = false;
}

async function init() {
  wire();
  const config = cfg();
  if (!config.supabaseReady) return;
  try {
    await loadSdk();
    account.client = window.supabase.createClient(config.url, config.key);
    const { data } = await account.client.auth.getSession();
    account.session = data.session;
    setAuthButton();
    account.client.auth.onAuthStateChange((_event, session) => {
      account.session = session;
      setAuthButton();
      refreshStatus();
      refreshDiary();
      const hint = document.getElementById("diaryHint");
      if (hint) hint.hidden = Boolean(session);
    });
    await refreshStatus();
    const hint = document.getElementById("diaryHint");
    if (hint) hint.hidden = Boolean(account.session);
  } catch (error) {
    console.warn(error);
    account.client = null;
    renderQuota(localStatus());
  }
}

window.CaloricAccount = {
  init,
  isLoggedIn: () => Boolean(account.session),
  async beforeScan() {
    try {
      const status = await consumeScan();
      if (!status.allowed) {
        openPaywall();
        return false;
      }
      return true;
    } catch (error) {
      console.warn(error);
      const formError = document.getElementById("formError");
      if (formError) {
        formError.hidden = false;
        formError.textContent = "Не удалось проверить лимит. Попробуй ещё раз.";
      }
      return false;
    }
  },
  afterScan,
  renderDiary: refreshDiary,
  async clearToday(start, end) {
    if (!account.client || !account.session) return;
    await account.client.from("scans").delete().gte("created_at", start).lt("created_at", end);
    await refreshDiary();
  },
};

init();
