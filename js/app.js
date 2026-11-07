/* Caloric Scan v2: ингредиенты FoodSeg103 + название блюда Food-101. */
const DIARY_KEY = "caloric-scan-diary-v1";
const THEME_KEY = "caloric-theme";
const PLATE_CM = 26;
const MODEL_REV = "v3";
const WASM_PATH = "https://cdn.jsdelivr.net/npm/@tensorflow/tfjs-backend-wasm@4.22.0/dist/";

const els = {
  themeBtn: document.getElementById("themeBtn"),
  dropzone: document.getElementById("dropzone"),
  file: document.getElementById("file"),
  loadFill: document.getElementById("loadFill"),
  loadText: document.getElementById("loadText"),
  formError: document.getElementById("formError"),
  analyzeBar: document.getElementById("analyzeBar"),
  analyzeText: document.getElementById("analyzeText"),
  retryBtn: document.getElementById("retryBtn"),
  result: document.getElementById("result"),
  photoCard: document.getElementById("photoCard"),
  canvas: document.getElementById("photoCanvas"),
  scanline: document.getElementById("scanline"),
  photoStatus: document.getElementById("photoStatus"),
  resultCard: document.getElementById("resultCard"),
  lowConf: document.getElementById("lowConf"),
  dishGuess: document.getElementById("dishGuess"),
  ingList: document.getElementById("ingList"),
  vKcal: document.getElementById("vKcal"),
  vP: document.getElementById("vP"),
  vF: document.getElementById("vF"),
  vC: document.getElementById("vC"),
  donutSegs: document.getElementById("donutSegs"),
  donutMid: document.getElementById("donutMid"),
  micro: document.getElementById("micro"),
  addBtn: document.getElementById("addBtn"),
  diaryTitle: document.getElementById("diaryTitle"),
  goal: document.getElementById("goal"),
  goalFill: document.getElementById("goalFill"),
  goalText: document.getElementById("goalText"),
  dayMacros: document.getElementById("dayMacros"),
  diaryList: document.getElementById("diaryList"),
  diaryEmpty: document.getElementById("diaryEmpty"),
  clearDay: document.getElementById("clearDay"),
  modelNote: document.getElementById("modelNote"),
};

const state = {
  dish: null,
  seg: null,
  labels: [],
  ingredients: [],
  segMeta: null,
  ready: false,
  meal: null,
  backend: "webgl",
};

const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const NS = "http://www.w3.org/2000/svg";

function fmt(n, digits) {
  return Number(n).toLocaleString("ru-RU", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function todayKey(date = new Date()) {
  const z = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${z(date.getMonth() + 1)}-${z(date.getDate())}`;
}

function todayLabel() {
  return new Date().toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
}

function plural(n, forms) {
  const n10 = n % 10;
  const n100 = n % 100;
  if (n10 === 1 && n100 !== 11) return forms[0];
  if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return forms[1];
  return forms[2];
}

function colorFor(id) {
  const h = (id * 47) % 360;
  return `hsl(${h} 72% 46%)`;
}

function loadDiary() {
  try {
    const raw = JSON.parse(localStorage.getItem(DIARY_KEY));
    if (!raw || typeof raw !== "object") throw new Error("empty");
    if (typeof raw.goal !== "number" || !Number.isFinite(raw.goal)) raw.goal = 2000;
    if (!raw.days || typeof raw.days !== "object") raw.days = {};
    return raw;
  } catch {
    return { goal: 2000, days: {} };
  }
}

function saveDiary(data) {
  localStorage.setItem(DIARY_KEY, JSON.stringify(data));
}

function setError(text) {
  els.formError.hidden = !text;
  els.formError.textContent = text || "";
  if (els.retryBtn) els.retryBtn.hidden = !text;
}

function showWorking(text) {
  const message = text || "Анализирую…";
  if (els.analyzeBar) els.analyzeBar.hidden = false;
  if (els.analyzeText) els.analyzeText.textContent = message;
  if (els.retryBtn) els.retryBtn.hidden = true;
  setError("");
  els.result.hidden = false;
  els.photoCard.hidden = false;
  els.scanline.hidden = false;
  els.photoStatus.hidden = false;
  els.photoStatus.textContent = message;
  els.photoCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

function hideWorking() {
  if (els.analyzeBar) els.analyzeBar.hidden = true;
  els.scanline.hidden = true;
  els.photoStatus.hidden = true;
}

function setLoad(percent, text) {
  els.loadFill.style.width = Math.max(0, Math.min(100, percent)) + "%";
  els.loadText.textContent = text;
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_KEY, theme);
  const dark = theme === "dark";
  els.themeBtn.setAttribute("aria-pressed", String(dark));
  els.themeBtn.setAttribute("aria-label", dark ? "Включить светлую тему" : "Включить тёмную тему");
  els.themeBtn.textContent = dark ? "☀️" : "🌙";
}

function burst(origin) {
  if (reduceMotion) return;
  const rect = origin.getBoundingClientRect();
  const layer = document.createElement("div");
  layer.className = "confetti";
  const bits = ["🍋", "🍅", "🥑", "✨", "🍓", "🍚"];
  for (let i = 0; i < 18; i += 1) {
    const piece = document.createElement("i");
    piece.textContent = bits[i % bits.length];
    piece.style.left = rect.left + rect.width / 2 + "px";
    piece.style.top = rect.top + 20 + "px";
    const ang = Math.random() * Math.PI * 2;
    const dist = 70 + Math.random() * 120;
    piece.style.setProperty("--dx", Math.cos(ang) * dist + "px");
    piece.style.setProperty("--dy", Math.sin(ang) * dist - 20 + "px");
    layer.appendChild(piece);
  }
  document.body.appendChild(layer);
  setTimeout(() => layer.remove(), 950);
}

function countTo(el, target, digits) {
  const token = Number(el.dataset.anim || "0") + 1;
  el.dataset.anim = String(token);
  if (reduceMotion) {
    el.textContent = fmt(target, digits);
    return;
  }
  const start = performance.now();
  const from = Number(String(el.textContent).replace(/\s/g, "").replace(",", ".")) || 0;
  function frame(now) {
    if (el.dataset.anim !== String(token)) return;
    const k = Math.min(1, (now - start) / 620);
    const eased = 1 - (1 - k) ** 3;
    el.textContent = fmt(from + (target - from) * eased, digits);
    if (k < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

function drawDonut(protein, fat, carbs) {
  const parts = [
    { cls: "seg-p", kcal: protein * 4 },
    { cls: "seg-f", kcal: fat * 9 },
    { cls: "seg-c", kcal: carbs * 4 },
  ];
  const sum = parts.reduce((acc, part) => acc + part.kcal, 0);
  els.donutSegs.replaceChildren();
  if (sum <= 0) {
    els.donutMid.textContent = "—";
    return;
  }
  const radius = 46;
  const circ = 2 * Math.PI * radius;
  const active = parts.filter((part) => part.kcal > 0).length;
  const gap = active > 1 ? 3 : 0;
  let offset = 0;
  for (const part of parts) {
    if (part.kcal <= 0) continue;
    const len = Math.max(0, (part.kcal / sum) * circ - gap);
    const circle = document.createElementNS(NS, "circle");
    circle.setAttribute("cx", "60");
    circle.setAttribute("cy", "60");
    circle.setAttribute("r", String(radius));
    circle.setAttribute("class", "seg " + part.cls);
    circle.setAttribute("stroke-dasharray", `${len} ${circ - len}`);
    circle.setAttribute("stroke-dashoffset", String(-offset));
    els.donutSegs.appendChild(circle);
    offset += len + gap;
  }
  const top = parts.slice().sort((a, b) => b.kcal - a.kcal)[0];
  const names = { "seg-p": "белки", "seg-f": "жиры", "seg-c": "углеводы" };
  els.donutMid.textContent = Math.round((top.kcal / sum) * 100) + "% " + names[top.cls];
}

function renderDiary() {
  if (window.CaloricAccount?.renderDiary) {
    window.CaloricAccount.renderDiary();
    return;
  }
  const data = loadDiary();
  const key = todayKey();
  const items = data.days[key] || [];
  els.goal.value = String(data.goal);
  els.diaryTitle.textContent = "Дневник · " + todayLabel();
  const totals = items.reduce((acc, item) => {
    acc.kcal += Number(item.kcal) || 0;
    acc.protein += Number(item.protein) || 0;
    acc.fat += Number(item.fat) || 0;
    acc.carbs += Number(item.carbs) || 0;
    return acc;
  }, { kcal: 0, protein: 0, fat: 0, carbs: 0 });
  const pct = data.goal > 0 ? Math.min(100, (totals.kcal / data.goal) * 100) : 0;
  els.goalFill.style.width = pct + "%";
  els.goalFill.parentElement.classList.toggle("over", totals.kcal > data.goal);
  els.goalText.textContent = totals.kcal <= data.goal
    ? fmt(totals.kcal, 0) + " из " + fmt(data.goal, 0) + " ккал · осталось " + fmt(Math.max(0, data.goal - totals.kcal), 0)
    : fmt(totals.kcal, 0) + " из " + fmt(data.goal, 0) + " ккал · цель превышена на " + fmt(totals.kcal - data.goal, 0);
  els.dayMacros.textContent = items.length
    ? "Белки " + fmt(totals.protein, 1) + " г · Жиры " + fmt(totals.fat, 1) + " г · Углеводы " + fmt(totals.carbs, 1) + " г"
    : "";
  els.diaryEmpty.hidden = items.length > 0;
  els.diaryList.replaceChildren();
  for (const item of items) {
    const li = document.createElement("li");
    const text = document.createElement("div");
    const title = document.createElement("b");
    title.textContent = item.nameRu || "Тарелка";
    const meta = document.createElement("small");
    meta.textContent = (item.detail ? item.detail + " · " : "") + fmt(item.kcal, 0) + " ккал";
    text.append(title, meta);
    const del = document.createElement("button");
    del.type = "button";
    del.className = "del";
    del.setAttribute("aria-label", "Удалить запись");
    del.textContent = "✕";
    del.addEventListener("click", () => {
      const fresh = loadDiary();
      fresh.days[key] = (fresh.days[key] || []).filter((row) => row.id !== item.id);
      saveDiary(fresh);
      renderDiary();
    });
    li.append(text, del);
    els.diaryList.appendChild(li);
  }
}

function componentsOf(classes, w, h, id) {
  const seen = new Uint8Array(w * h);
  const parts = [];
  for (let i = 0; i < w * h; i += 1) {
    if (classes[i] !== id || seen[i]) continue;
    const stack = [i];
    seen[i] = 1;
    let count = 0;
    let sx = 0;
    let sy = 0;
    while (stack.length) {
      const p = stack.pop();
      const x = p % w;
      const y = (p / w) | 0;
      count += 1;
      sx += x;
      sy += y;
      if (x > 0 && !seen[p - 1] && classes[p - 1] === id) { seen[p - 1] = 1; stack.push(p - 1); }
      if (x + 1 < w && !seen[p + 1] && classes[p + 1] === id) { seen[p + 1] = 1; stack.push(p + 1); }
      if (y > 0 && !seen[p - w] && classes[p - w] === id) { seen[p - w] = 1; stack.push(p - w); }
      if (y + 1 < h && !seen[p + w] && classes[p + w] === id) { seen[p + w] = 1; stack.push(p + w); }
    }
    parts.push({ count, cx: sx / count, cy: sy / count });
  }
  return parts;
}

function plateScale(classes, w, h) {
  let minX = w;
  let minY = h;
  let maxX = 0;
  let maxY = 0;
  let food = 0;
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      if (!classes[y * w + x]) continue;
      food += 1;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
  }
  if (!food) return null;
  const bw = Math.max(1, maxX - minX + 1);
  const bh = Math.max(1, maxY - minY + 1);
  const span = Math.max(bw, bh);
  const frame = Math.min(w, h);
  const diameter = span > frame * 0.82 ? frame : Math.min(frame, span * 1.18);
  return { cmPerPx: PLATE_CM / diameter, food, minX, minY, maxX, maxY };
}

function paint(work, classes, partsById) {
  const canvas = els.canvas;
  const ctx = canvas.getContext("2d");
  const dw = Math.max(1, Math.round(work.w));
  const dh = Math.max(1, Math.round(work.h));
  canvas.width = dw;
  canvas.height = dh;
  ctx.drawImage(work.canvas, work.x, work.y, work.w, work.h, 0, 0, dw, dh);
  const cell = work.size / classes.w;
  ctx.globalAlpha = 0.42;
  for (let y = 0; y < classes.h; y += 1) {
    for (let x = 0; x < classes.w; x += 1) {
      const id = classes.data[y * classes.w + x];
      if (!id) continue;
      const px = x * cell;
      const py = y * cell;
      if (px + cell < work.x || py + cell < work.y || px > work.x + work.w || py > work.y + work.h) continue;
      ctx.fillStyle = colorFor(id);
      ctx.fillRect(px - work.x, py - work.y, Math.ceil(cell), Math.ceil(cell));
    }
  }
  ctx.globalAlpha = 1;
  ctx.font = "bold 16px Manrope, sans-serif";
  ctx.textAlign = "center";
  ctx.lineWidth = 3;
  for (const [id, parts] of partsById) {
    const info = state.ingredients[id];
    if (!info) continue;
    const main = parts.slice().sort((a, b) => b.count - a.count)[0];
    if (!main || main.count < 6) continue;
    const tx = (main.cx + 0.5) * cell - work.x;
    const ty = (main.cy + 0.5) * cell - work.y;
    if (tx < 8 || ty < 16 || tx > dw - 8 || ty > dh - 8) continue;
    ctx.strokeStyle = "rgba(0,0,0,0.55)";
    ctx.fillStyle = "#fff";
    ctx.strokeText(info.ru, tx, ty);
    ctx.fillText(info.ru, tx, ty);
  }
}

function renderMeal(meal, animate) {
  state.meal = meal;
  els.result.hidden = false;
  els.resultCard.hidden = false;
  els.photoCard.hidden = false;
  els.lowConf.hidden = !meal.low;
  els.lowConf.textContent = meal.low
    ? "Разметка неуверенная — смотри на неё как на ориентир."
    : "";
  if (meal.dish) {
    els.dishGuess.hidden = false;
    els.dishGuess.textContent = "Похоже на: " + meal.dish.ru + " · " + fmt(meal.dish.conf * 100, 0) + "%";
  } else {
    els.dishGuess.hidden = true;
  }
  els.ingList.replaceChildren();
  for (const row of meal.rows) {
    const li = document.createElement("li");
    const emo = document.createElement("span");
    emo.className = "emo";
    emo.textContent = row.emoji;
    const text = document.createElement("div");
    const title = document.createElement("b");
    title.textContent = row.ru;
    const meta = document.createElement("small");
    const bits = [];
    if (row.countLabel) bits.push(row.countLabel);
    bits.push("~" + fmt(row.grams, 0) + " г");
    meta.textContent = bits.join(" · ");
    text.append(title, meta);
    const kcal = document.createElement("span");
    kcal.className = "ing-kcal";
    kcal.textContent = fmt(row.kcal, 0);
    li.append(emo, text, kcal);
    els.ingList.appendChild(li);
  }
  if (animate) {
    countTo(els.vKcal, meal.kcal, 0);
    countTo(els.vP, meal.protein, 1);
    countTo(els.vF, meal.fat, 1);
    countTo(els.vC, meal.carbs, 1);
  } else {
    els.vKcal.textContent = fmt(meal.kcal, 0);
    els.vP.textContent = fmt(meal.protein, 1);
    els.vF.textContent = fmt(meal.fat, 1);
    els.vC.textContent = fmt(meal.carbs, 1);
  }
  drawDonut(meal.protein, meal.fat, meal.carbs);
  const fiber = meal.rows.reduce((sum, row) => sum + row.fiber, 0);
  const sugar = meal.rows.reduce((sum, row) => sum + row.sugar, 0);
  const micro = [];
  if (fiber > 0.2) micro.push("Клетчатка " + fmt(fiber, 1) + " г");
  if (sugar > 0.2) micro.push("Сахар " + fmt(sugar, 1) + " г");
  els.micro.textContent = micro.join(" · ");
}

async function addToDiary() {
  const meal = state.meal;
  if (!meal || !meal.rows.length) return;
  if (window.CaloricAccount?.isLoggedIn()) {
    await window.CaloricAccount.afterScan(meal);
    burst(els.addBtn);
    els.addBtn.textContent = "В дневнике ✓";
    setTimeout(() => { els.addBtn.textContent = "Добавить в дневник"; }, 900);
    return;
  }
  const data = loadDiary();
  const key = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const names = meal.rows.map((row) => row.ru);
  data.days[key] = data.days[key] || [];
  data.days[key].push({
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    nameRu: meal.dish ? meal.dish.ru : "Тарелка",
    detail: names.slice(0, 4).join(", "),
    kcal: meal.kcal,
    protein: meal.protein,
    fat: meal.fat,
    carbs: meal.carbs,
    grams: meal.rows.reduce((sum, row) => sum + row.grams, 0),
    ts: Date.now(),
  });
  saveDiary(data);
  renderDiary();
  burst(els.addBtn);
  els.addBtn.textContent = "В дневнике ✓";
  setTimeout(() => { els.addBtn.textContent = "Добавить в дневник"; }, 900);
}

function letterbox(bitmap, size) {
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#14120f";
  ctx.fillRect(0, 0, size, size);
  const scale = Math.min(size / bitmap.width, size / bitmap.height);
  const w = bitmap.width * scale;
  const h = bitmap.height * scale;
  const x = (size - w) / 2;
  const y = (size - h) / 2;
  ctx.drawImage(bitmap, x, y, w, h);
  return { canvas, size, x, y, w, h };
}

function unwrapOutput(out) {
  if (!out) return out;
  if (typeof out.argMax === "function" || typeof out.data === "function") return out;
  if (out.probs) return out.probs;
  if (out.logits) return out.logits;
  if (out.output_0) return out.output_0;
  if (Array.isArray(out)) return unwrapOutput(out[0]);
  const values = Object.values(out);
  if (values.length === 1) return unwrapOutput(values[0]);
  return out;
}

function predictImage(model, canvas, size) {
  return tf.tidy(() => {
    let img = tf.browser.fromPixels(canvas);
    if (size) img = tf.image.resizeBilinear(img, [size, size]);
    const input = img.toFloat().expandDims(0);
    let out;
    try {
      out = model.predict(input);
    } catch (err) {
      const inputName = model.inputs?.[0]?.name || "image";
      const short = String(inputName).split(":")[0];
      out = model.execute({ [short]: input, [inputName]: input });
      console.warn(err);
    }
    return unwrapOutput(out);
  });
}

async function analyze(bitmap) {
  const segSize = state.segMeta?.img_size || 224;
  const work = letterbox(bitmap, segSize);
  els.canvas.width = segSize;
  els.canvas.height = segSize;
  els.canvas.getContext("2d").drawImage(work.canvas, 0, 0);
  showWorking("Анализирую…");

  const logits = predictImage(state.seg, work.canvas);
  const classTensor = logits.argMax(-1);
  const confTensor = tf.tidy(() => logits.softmax().max(-1));
  const classData = await classTensor.data();
  const confData = await confTensor.data();
  const shape = classTensor.shape;
  const gridH = shape[shape.length - 2];
  const gridW = shape[shape.length - 1];
  logits.dispose();
  classTensor.dispose();
  confTensor.dispose();

  const classes = { data: classData, w: gridW, h: gridH };
  const scale = plateScale(classData, gridW, gridH);
  if (!scale) {
    hideWorking();
    setError("Не вижу еды на фото. Попробуй снимок тарелки сверху.");
    bitmap.close?.();
    return null;
  }
  const rows = [];
  const partsById = new Map();
  let confSum = 0;
  let confN = 0;
  for (let i = 0; i < classData.length; i += 1) {
    if (classData[i]) {
      confSum += confData[i];
      confN += 1;
    }
  }
  const meanConf = confN ? confSum / confN : 0;
  for (let id = 1; id < state.ingredients.length; id += 1) {
    const info = state.ingredients[id];
    if (!info) continue;
    const parts = componentsOf(classData, gridW, gridH, id).filter((part) => part.count >= 6);
    const pixels = parts.reduce((sum, part) => sum + part.count, 0);
    const gramsRaw = pixels * scale.cmPerPx * scale.cmPerPx * info.g_per_cm2;
    // Мелкие пятна маски не показываем: это шум сетки 56×56, не порция.
    if (gramsRaw < 8 || pixels < 8) continue;
    partsById.set(id, parts);
    const grams = Math.min(600, gramsRaw);
    const k = grams / 100;
    let countLabel = "";
    if (info.countable && parts.length) {
      countLabel = parts.length + " " + plural(parts.length, info.unit || ["шт.", "шт.", "шт."]);
    }
    rows.push({
      id,
      ru: info.ru,
      emoji: info.emoji,
      grams,
      kcal: info.kcal * k,
      protein: info.protein * k,
      fat: info.fat * k,
      carbs: info.carbs * k,
      fiber: (info.fiber || 0) * k,
      sugar: (info.sugar || 0) * k,
      countLabel,
    });
  }
  rows.sort((a, b) => b.kcal - a.kcal);
  paint(work, classes, partsById);

  let dish = null;
  if (state.dish) {
    const dishSize = state.dish.inputs?.[0]?.shape?.[1] || 160;
    const scores = predictImage(state.dish, work.canvas, dishSize);
    const data = await scores.data();
    scores.dispose();
    let best = 0;
    for (let i = 1; i < data.length; i += 1) if (data[i] > data[best]) best = i;
    if (data[best] >= 0.4 && state.labels[best]) dish = { ...state.labels[best], conf: data[best] };
  }

  hideWorking();
  bitmap.close?.();
  const meal = {
    rows,
    dish,
    low: meanConf < 0.4,
    kcal: rows.reduce((sum, row) => sum + row.kcal, 0),
    protein: rows.reduce((sum, row) => sum + row.protein, 0),
    fat: rows.reduce((sum, row) => sum + row.fat, 0),
    carbs: rows.reduce((sum, row) => sum + row.carbs, 0),
  };
  if (!rows.length) {
    setError("Ингредиенты не разобрались. Попробуй другое фото.");
    return null;
  }
  setError("");
  renderMeal(meal, true);
  burst(els.resultCard);
  return meal;
}

function looksLikeImage(file) {
  const type = (file.type || "").toLowerCase();
  const name = (file.name || "").toLowerCase();
  if (!type) return true;
  if (type.startsWith("image/")) return true;
  return /\.(jpe?g|png|webp|gif|heic|heif|bmp|avif)$/.test(name);
}

async function fileToBitmap(file) {
  if (!file || !looksLikeImage(file)) {
    throw new Error("Нужно фото: JPG, PNG или WEBP.");
  }
  const maxSide = 1600;
  try {
    const probe = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, maxSide / Math.max(probe.width, probe.height));
    if (scale >= 1) return probe;
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(probe.width * scale));
    canvas.height = Math.max(1, Math.round(probe.height * scale));
    canvas.getContext("2d").drawImage(probe, 0, 0, canvas.width, canvas.height);
    probe.close?.();
    return createImageBitmap(canvas);
  } catch {
    throw new Error("Не удалось открыть это фото. Если оно в HEIC, сохрани его как JPG и попробуй снова.");
  }
}

async function switchBackendAndReload() {
  const order = ["wasm", "cpu"].filter((name) => name !== state.backend);
  order.unshift(state.backend === "webgl" ? "wasm" : "cpu");
  const next = [...new Set(order)];
  for (const name of next) {
    try {
      await activateBackend(name);
      state.backend = name;
      await reloadModels();
      return true;
    } catch (err) {
      console.warn(name, err);
    }
  }
  return false;
}

async function handleFile(file) {
  setError("");
  if (!state.ready) {
    setError("Нейросеть ещё загружается. Подожди пару секунд и нажми «Попробовать ещё раз».");
    return;
  }
  if (!file || !looksLikeImage(file)) {
    setError("Нужно фото: JPG, PNG или WEBP.");
    return;
  }
  showWorking("Анализирую…");
  let bitmap;
  try {
    bitmap = await fileToBitmap(file);
  } catch (err) {
    hideWorking();
    setError(err.message || "Не удалось открыть фото.");
    return;
  }
  if (window.CaloricAccount) {
    let allowed = true;
    try {
      allowed = await window.CaloricAccount.beforeScan();
    } catch (err) {
      console.warn(err);
      allowed = true;
    }
    if (!allowed) {
      hideWorking();
      bitmap.close?.();
      return;
    }
  }
  try {
    let meal = null;
    try {
      meal = await analyze(bitmap);
    } catch (err) {
      console.error(err);
      bitmap = await fileToBitmap(file).catch(() => null);
      const switched = bitmap && await switchBackendAndReload();
      if (!switched || !bitmap) throw err;
      showWorking("Анализирую ещё раз, уже без видеокарты…");
      meal = await analyze(bitmap);
    }
    if (meal && window.CaloricAccount) {
      try { await window.CaloricAccount.afterScan(meal); } catch (err) { console.warn(err); }
    }
  } catch (err) {
    console.error(err);
    hideWorking();
    const detail = err && err.message ? " (" + String(err.message).slice(0, 140) + ")" : "";
    setError("Не получилось разобрать фото" + detail + ". Нажми «Попробовать ещё раз».");
  }
}

async function activateBackend(name) {
  if (name === "wasm" && tf.wasm?.setWasmPaths) tf.wasm.setWasmPaths(WASM_PATH);
  await tf.setBackend(name);
  await tf.ready();
  const probe = tf.scalar(1);
  await probe.data();
  probe.dispose();
}

async function prepareBackend() {
  const order = ["webgl", "wasm", "cpu"];
  let lastError = null;
  for (const name of order) {
    try {
      await activateBackend(name);
      state.backend = name;
      return name;
    } catch (err) {
      lastError = err;
      console.warn(name, err);
    }
  }
  throw lastError || new Error("backend");
}

async function forgetStaleModels() {
  const stale = [
    "indexeddb://caloric-scan-v1",
    "indexeddb://caloric-dish-v1",
    "indexeddb://caloric-seg-v2",
  ];
  await Promise.all(stale.map((key) => tf.io.removeModel(key).catch(() => {})));
}

async function loadGraph(url, idbKey, onProgress) {
  try {
    return await tf.loadGraphModel(idbKey);
  } catch {
    const model = await tf.loadGraphModel(url, { onProgress });
    try { await model.save(idbKey); } catch (err) { console.warn(err); }
    return model;
  }
}

async function reloadModels() {
  const dishUrl = new URL("model/model.json", document.baseURI).href;
  const segUrl = new URL("model/seg/model.json", document.baseURI).href;
  state.dish = await loadGraph(dishUrl, "indexeddb://caloric-dish-" + MODEL_REV);
  state.seg = await loadGraph(segUrl, "indexeddb://caloric-seg-" + MODEL_REV);
}

async function boot() {
  applyTheme(localStorage.getItem(THEME_KEY) || "light");
  renderDiary();
  const [labels, ingredients, segMeta] = await Promise.all([
    fetch(new URL("data/labels.json", document.baseURI)).then((r) => r.json()),
    fetch(new URL("data/ingredients.json", document.baseURI)).then((r) => r.json()),
    fetch(new URL("model/seg/metadata.json", document.baseURI)).then((r) => (r.ok ? r.json() : null)),
  ]);
  state.labels = labels;
  state.ingredients = ingredients.items;
  state.segMeta = segMeta;
  await prepareBackend();
  await forgetStaleModels();
  const dishUrl = new URL("model/model.json", document.baseURI).href;
  const segUrl = new URL("model/seg/model.json", document.baseURI).href;
  setLoad(5, "Загружаем модель блюда…");
  state.dish = await loadGraph(dishUrl, "indexeddb://caloric-dish-" + MODEL_REV, (frac) => {
    setLoad(5 + frac * 40, "Загружаем модель блюда… " + Math.round(frac * 100) + "%");
  });
  setLoad(50, "Загружаем модель ингредиентов…");
  state.seg = await loadGraph(segUrl, "indexeddb://caloric-seg-" + MODEL_REV, (frac) => {
    setLoad(50 + frac * 50, "Загружаем модель ингредиентов… " + Math.round(frac * 100) + "%");
  });
  state.ready = true;
  setLoad(100, "Нейросеть готова — добавь фото");
  if (segMeta) {
    const miou = fmt((segMeta.miou_food || 0) * 100, 1);
    const top1 = fmt((segMeta.food101_test_top1 || 0) * 100, 1);
    els.modelNote.textContent = "Ингредиенты: FoodSeg103, mIoU " + miou
      + "%. Название блюда: Food-101, top-1 " + top1 + "%.";
  }
}

els.themeBtn.addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});
els.file.addEventListener("change", () => {
  const file = els.file.files && els.file.files[0];
  els.file.value = "";
  if (file) handleFile(file);
});
["dragenter", "dragover"].forEach((name) => {
  els.dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    els.dropzone.classList.add("drag");
  });
});
["dragleave", "drop"].forEach((name) => {
  els.dropzone.addEventListener(name, (event) => {
    event.preventDefault();
    if (name === "dragleave") els.dropzone.classList.remove("drag");
  });
});
els.dropzone.addEventListener("drop", (event) => {
  els.dropzone.classList.remove("drag");
  const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
  if (file) handleFile(file);
});
els.addBtn.addEventListener("click", addToDiary);
els.retryBtn?.addEventListener("click", () => els.file?.click());
window.addEventListener("unhandledrejection", (event) => {
  console.error(event.reason);
  setError("Что-то пошло не так при разборе фото. Нажми «Попробовать ещё раз».");
  hideWorking();
});
els.goal.addEventListener("change", () => {
  const data = loadDiary();
  const goal = Number(els.goal.value);
  data.goal = Number.isFinite(goal) ? Math.min(8000, Math.max(800, Math.round(goal))) : 2000;
  els.goal.value = String(data.goal);
  saveDiary(data);
  renderDiary();
});
els.clearDay.addEventListener("click", async () => {
  if (window.CaloricAccount?.isLoggedIn()) {
    const day = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
    const [year, month, date] = day.split("-").map(Number);
    const start = new Date(Date.UTC(year, month - 1, date, -3, 0, 0)).toISOString();
    const end = new Date(Date.UTC(year, month - 1, date, 21, 0, 0)).toISOString();
    const client = window.CaloricAccount;
    if (client.clearToday) await client.clearToday(start, end);
    renderDiary();
    return;
  }
  const data = loadDiary();
  const key = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  data.days[key] = [];
  saveDiary(data);
  renderDiary();
});

boot().catch((err) => {
  console.error(err);
  setLoad(0, "Не удалось загрузить модель");
  setError("Не удалось загрузить модель. Проверь интернет и обнови страницу.");
});
