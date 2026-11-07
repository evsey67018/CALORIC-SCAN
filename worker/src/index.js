// Оплата Caloric Scan. Провайдер сейчас один — ЮKassa.
// Секреты только из env (wrangler secret / .dev.vars), в репозитории их нет.

const PLANS = {
  month: { amount: "199.00", days: 30, label: "Caloric Scan Premium, 30 дней, СБП" },
  year: { amount: "1299.00", days: 365, label: "Caloric Scan Premium, 365 дней, СБП" },
};

function corsHeaders(request, env) {
  const origin = request.headers.get("Origin") || "";
  const allowed = String(env.ALLOWED_ORIGINS || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const ok = allowed.includes(origin) ? origin : allowed[0] || "";
  return {
    "access-control-allow-origin": ok,
    "access-control-allow-methods": "POST, OPTIONS",
    "access-control-allow-headers": "authorization, content-type",
    "access-control-max-age": "86400",
    vary: "Origin",
  };
}

function json(request, env, body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...corsHeaders(request, env) },
  });
}

function paymentsConfigured(env) {
  return Boolean(env.YOOKASSA_SHOP_ID && env.YOOKASSA_SECRET_KEY && env.SUPABASE_URL && env.SUPABASE_SERVICE_ROLE_KEY && env.SUPABASE_ANON_KEY);
}

function yookassaAuth(env) {
  return "Basic " + btoa(`${env.YOOKASSA_SHOP_ID}:${env.YOOKASSA_SECRET_KEY}`);
}

async function yookassa(env, path, options = {}) {
  const response = await fetch("https://api.yookassa.ru/v3" + path, {
    ...options,
    headers: {
      authorization: yookassaAuth(env),
      "content-type": "application/json",
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!response.ok) {
    const error = new Error("yookassa");
    error.status = response.status;
    error.payload = data;
    throw error;
  }
  return data;
}

async function supabaseUser(env, accessToken) {
  const response = await fetch(`${env.SUPABASE_URL}/auth/v1/user`, {
    headers: {
      apikey: env.SUPABASE_ANON_KEY,
      authorization: `Bearer ${accessToken}`,
    },
  });
  if (!response.ok) return null;
  return response.json();
}

async function supabaseRest(env, path, options = {}) {
  const response = await fetch(`${env.SUPABASE_URL}/rest/v1${path}`, {
    ...options,
    headers: {
      apikey: env.SUPABASE_SERVICE_ROLE_KEY,
      authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
      "content-type": "application/json",
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!response.ok) {
    const error = new Error("supabase");
    error.status = response.status;
    error.payload = data;
    throw error;
  }
  return data;
}

function extendUntil(currentIso, days) {
  const now = Date.now();
  const current = currentIso ? new Date(currentIso).getTime() : 0;
  const start = Math.max(now, Number.isFinite(current) ? current : 0);
  return new Date(start + days * 24 * 60 * 60 * 1000).toISOString();
}

function safeReturnUrl(raw, env) {
  const allowed = String(env.ALLOWED_ORIGINS || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  try {
    const url = new URL(raw);
    if (allowed.includes(url.origin)) return url.href;
  } catch { /* не ссылка */ }
  return allowed.find((item) => item.startsWith("https://")) || allowed[0] || "https://evsey67018.github.io/CALORIC-SCAN/";
}

async function createPayment(request, env) {
  if (!paymentsConfigured(env)) {
    return json(request, env, { error: "payments_not_configured" }, 501);
  }
  const header = request.headers.get("Authorization") || "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : "";
  const user = token ? await supabaseUser(env, token) : null;
  if (!user?.id) return json(request, env, { error: "unauthorized" }, 401);

  const body = await request.json().catch(() => ({}));
  const plan = body.plan === "year" ? "year" : body.plan === "month" ? "month" : null;
  if (!plan) return json(request, env, { error: "bad_plan" }, 400);
  const price = PLANS[plan];
  const idempotence = crypto.randomUUID();
  const payment = await yookassa(env, "/payments", {
    method: "POST",
    headers: { "Idempotence-Key": idempotence },
    body: JSON.stringify({
      amount: { value: price.amount, currency: "RUB" },
      capture: true,
      confirmation: { type: "redirect", return_url: safeReturnUrl(body.return_url, env) },
      description: price.label,
      payment_method_data: { type: "sbp" },
      metadata: { user_id: user.id, plan },
    }),
  });

  await supabaseRest(env, "/payments", {
    method: "POST",
    headers: { Prefer: "return=minimal" },
    body: JSON.stringify({
      user_id: user.id,
      provider: "yookassa",
      provider_payment_id: payment.id,
      amount: Number(price.amount),
      plan,
      status: "pending",
    }),
  });

  return json(request, env, {
    confirmation_url: payment.confirmation?.confirmation_url || null,
    payment_id: payment.id,
  });
}

async function grantPremium(env, row) {
  const profiles = await supabaseRest(env, `/profiles?id=eq.${row.user_id}&select=premium_until`);
  const until = extendUntil(profiles?.[0]?.premium_until, PLANS[row.plan].days);
  await supabaseRest(env, `/profiles?id=eq.${row.user_id}`, {
    method: "PATCH",
    headers: { Prefer: "return=minimal" },
    body: JSON.stringify({ plan: "premium", premium_until: until }),
  });
  await supabaseRest(env, `/payments?id=eq.${row.id}`, {
    method: "PATCH",
    headers: { Prefer: "return=minimal" },
    body: JSON.stringify({ status: "succeeded" }),
  });
}

async function webhook(request, env) {
  if (!paymentsConfigured(env)) return json(request, env, { error: "payments_not_configured" }, 501);
  const note = await request.json().catch(() => null);
  const paymentId = note?.object?.id;
  if (!paymentId) return json(request, env, { ok: true });

  // Статусу из тела вебхука не верим: ещё раз спрашиваем ЮKassa.
  const payment = await yookassa(env, `/payments/${encodeURIComponent(paymentId)}`);
  const rows = await supabaseRest(
    env,
    `/payments?provider=eq.yookassa&provider_payment_id=eq.${encodeURIComponent(payment.id)}&select=id,user_id,plan,amount,status`,
  );
  const row = rows?.[0];
  if (!row) return json(request, env, { ok: true, ignored: true });
  if (row.status === "succeeded") return json(request, env, { ok: true, duplicate: true });

  const expected = PLANS[row.plan]?.amount;
  const paid = payment.amount?.value;
  const sameMoney = expected && Number(paid) === Number(expected) && payment.amount?.currency === "RUB";
  const sbp = payment.payment_method?.type === "sbp";
  if (payment.status === "succeeded" && payment.paid && sameMoney && sbp && payment.metadata?.user_id === row.user_id) {
    await grantPremium(env, row);
  } else if (payment.status === "canceled") {
    await supabaseRest(env, `/payments?id=eq.${row.id}`, {
      method: "PATCH",
      headers: { Prefer: "return=minimal" },
      body: JSON.stringify({ status: "canceled" }),
    });
  }
  return json(request, env, { ok: true });
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(request, env) });
    }
    const path = new URL(request.url).pathname;
    try {
      if (request.method === "POST" && path === "/create-payment") return await createPayment(request, env);
      if (request.method === "POST" && path === "/webhook") return await webhook(request, env);
      return json(request, env, { error: "not_found" }, 404);
    } catch (error) {
      console.error(error.status || "", error.message);
      return json(request, env, { error: "upstream" }, 502);
    }
  },
};
