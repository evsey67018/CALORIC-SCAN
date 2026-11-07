/* Caloric Scan: распознавание блюда моделью TFJS и подсчёт порции. */
const KEY = "caloric-scan-diary-v1";
const THEME_KEY = "caloric-theme";

const els = {
  themeBtn: document.getElementById("themeBtn"),
  dropzone: document.getElementById("dropzone"),
  pickBtn: document.getElementById("pickBtn"),
  shotBtn: document.getElementById("shotBtn"),
  file: document.getElementById("file"),
  shotFile: document.getElementById("shotFile"),
  loadFill: document.getElementById("loadFill"),
  loadText: document.getElementById("loadText"),
  formError: document.getElementById("formError"),
  result: document.getElementById("result"),
  photoCard: document.getElementById("photoCard"),
  photo: document.getElementById("photo"),
  scanline: document.getElementById("scanline"),
  photoStatus: document.getElementById("photoStatus"),
  resultCard: document.getElementById("resultCard"),
  lowConf: document.getElementById("lowConf"),
  dishRu: document.getElementById("dishRu"),
  dishEn: document.getElementById("dishEn"),
  confPill: document.getElementById("confPill"),
  alts: document.getElementById("alts"),
  altsLabel: document.getElementById("altsLabel"),
  gramsRange: document.getElementById("gramsRange"),
  gramsNum: document.getElementById("gramsNum"),
  per100: document.getElementById("per100"),
  vKcal: document.getElementById("vKcal"),
  vP: document.getElementById("vP"),
  vF: document.getElementById("vF"),
  vC: document.getElementById("vC"),
  donutSegs: document.getElementById("donutSegs"),
  donutMid: document.getElementById("donutMid"),
  micro: document.getElementById("micro"),
  addBtn: document.getElementById("addBtn"),
  fixBtn: document.getElementById("fixBtn"),
  manualOpen: document.getElementById("manualOpen"),
  diaryTitle: document.getElementById("diaryTitle"),
  goal: document.getElementById("goal"),
  goalFill: document.getElementById("goalFill"),
  goalText: document.getElementById("goalText"),
  dayMacros: document.getElementById("dayMacros"),
  diaryList: document.getElementById("diaryList"),
  diaryEmpty: document.getElementById("diaryEmpty"),
  clearDay: document.getElementById("clearDay"),
  modelNote: document.getElementById("modelNote"),
  searchDialog: document.getElementById("searchDialog"),
  searchInput: document.getElementById("searchInput"),
  searchList: document.getElementById("searchList"),
  cameraDialog: document.getElementById("cameraDialog"),
  video: document.getElementById("video"),
  shutter: document.getElementById("shutter"),
  closeCam: document.getElementById("closeCam"),
  useFileCam: document.getElementById("useFileCam"),
  camError: document.getElementById("camError"),
};

const state = {
  model: null,
  labels: [],
  nutrition: null,
  meta: null,
  probs: null,
  selected: -1,
  manual: false,
  imgSize: 160,
  objectUrl: null,
  stream: null,
  ready: false,
  busy: false,
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

function loadDiary() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY));
    if (!raw || typeof raw !== "object") throw new Error("empty");
    if (typeof raw.goal !== "number" || !Number.isFinite(raw.goal)) raw.goal = 2000;
    if (!raw.days || typeof raw.days !== "object") raw.days = {};
    return raw;
  } catch {
    return { goal: 2000, days: {} };
  }
}

function saveDiary(data) {
  localStorage.setItem(KEY, JSON.stringify(data));
}

function setError(text) {
  if (!text) {
    els.formError.hidden = true;
    els.formError.textContent = "";
    return;
  }
  els.formError.hidden = false;
  els.formError.textContent = text;
}

function setLoad(percent, text) {
  const p = Math.max(0, Math.min(100, percent));
  els.loadFill.style.width = p + "%";
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
  const bits = ["🍋", "🍅", "🥑", "✨", "🍓", "🥐"];
  for (let i = 0; i < 22; i += 1) {
    const piece = document.createElement("i");
    piece.textContent = bits[i % bits.length];
    piece.style.left = rect.left + rect.width / 2 + "px";
    piece.style.top = rect.top + 24 + "px";
    const ang = Math.random() * Math.PI * 2;
    const dist = 70 + Math.random() * 140;
    piece.style.setProperty("--dx", Math.cos(ang) * dist + "px");
    piece.style.setProperty("--dy", (Math.sin(ang) * dist - 30) + "px");
    layer.appendChild(piece);
  }
  document.body.appendChild(layer);
  setTimeout(() => layer.remove(), 950);
}

function countTo(el, target, digits) {
  const token = Number(el.dataset.anim || "0") + 1;
  el.dataset.anim = String(token);
  el.dataset.v = String(target);
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
    const value = from + (target - from) * eased;
    el.textContent = k < 1 ? fmt(value, digits) : fmt(target, digits);
    if (k < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

function foodOf(index) {
  const label = state.labels[index];
  if (!label) return null;
  const item = state.nutrition.items[label.id];
  if (!item) return null;
  return { label, item };
}

function currentGrams() {
  const n = Number(els.gramsNum.value);
  if (!Number.isFinite(n)) return 100;
  return Math.min(1500, Math.max(10, Math.round(n)));
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

function renderAlts() {
  els.alts.replaceChildren();
  if (!state.probs) {
    els.altsLabel.hidden = true;
    return;
  }
  els.altsLabel.hidden = false;
  const order = [...state.probs.keys()].sort((a, b) => state.probs[b] - state.probs[a]);
  const picks = order.filter((idx) => idx !== state.selected).slice(0, 3);
  for (const idx of picks) {
    const food = foodOf(idx);
    if (!food) continue;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "alt";
    const name = document.createElement("span");
    name.textContent = food.label.ru + " ";
    const pct = document.createElement("strong");
    pct.textContent = fmt(state.probs[idx] * 100, state.probs[idx] < 0.1 ? 1 : 0) + "%";
    btn.append(name, pct);
    btn.addEventListener("click", () => selectDish(idx, false, true));
    els.alts.appendChild(btn);
  }
}

function renderResult(animate) {
  const food = foodOf(state.selected);
  if (!food) return;
  const grams = currentGrams();
  const scale = grams / 100;
  const kcal = food.item.kcal * scale;
  const protein = food.item.protein * scale;
  const fat = food.item.fat * scale;
  const carbs = food.item.carbs * scale;
  els.result.hidden = false;
  els.resultCard.hidden = false;
  els.dishRu.textContent = food.label.ru;
  els.dishEn.textContent = food.label.en;
  const conf = state.probs ? state.probs[state.selected] : null;
  if (state.manual) {
    const tail = conf == null ? "" : " · модель " + fmt(conf * 100, 1) + "%";
    els.confPill.textContent = "вручную" + tail;
  } else if (conf != null) {
    els.confPill.textContent = fmt(conf * 100, conf < 0.1 ? 1 : 0) + "%";
  } else {
    els.confPill.textContent = "—";
  }
  const unsure = !state.manual && conf != null && conf < 0.3;
  els.lowConf.hidden = !unsure;
  if (unsure) {
    els.lowConf.textContent = "Уверенность ниже 30%. Выбери похожий вариант ниже или найди блюдо в списке — так цифры будут честнее.";
  }
  els.per100.textContent = "На 100 г: " + fmt(food.item.kcal, 0) + " ккал";
  if (animate) {
    countTo(els.vKcal, kcal, 0);
    countTo(els.vP, protein, 1);
    countTo(els.vF, fat, 1);
    countTo(els.vC, carbs, 1);
  } else {
    for (const el of [els.vKcal, els.vP, els.vF, els.vC]) {
      el.dataset.anim = String(Number(el.dataset.anim || "0") + 1);
    }
    els.vKcal.textContent = fmt(kcal, 0);
    els.vP.textContent = fmt(protein, 1);
    els.vF.textContent = fmt(fat, 1);
    els.vC.textContent = fmt(carbs, 1);
  }
  drawDonut(protein, fat, carbs);
  const bits = [];
  if (food.item.fiber) bits.push("Клетчатка " + fmt(food.item.fiber * scale, 1) + " г");
  if (food.item.sugar) bits.push("Сахар " + fmt(food.item.sugar * scale, 1) + " г");
  els.micro.textContent = bits.join(" · ");
  renderAlts();
  els.resultCard.dataset.kcal = String(kcal);
  els.resultCard.dataset.protein = String(protein);
  els.resultCard.dataset.fat = String(fat);
  els.resultCard.dataset.carbs = String(carbs);
  els.resultCard.dataset.fiber = String((food.item.fiber || 0) * scale);
  els.resultCard.dataset.sugar = String((food.item.sugar || 0) * scale);
}

function selectDish(index, manual, animate) {
  state.selected = index;
  state.manual = manual;
  const food = foodOf(index);
  if (food && animate) {
    const portion = food.item.portion_g || 100;
    els.gramsRange.value = String(Math.min(1000, portion));
    els.gramsNum.value = String(portion);
  }
  renderResult(animate);
  if (animate) burst(els.resultCard);
}

function syncGrams(fromRange) {
  if (fromRange) els.gramsNum.value = els.gramsRange.value;
  else {
    const grams = currentGrams();
    els.gramsNum.value = String(grams);
    els.gramsRange.value = String(Math.min(1000, Math.max(20, grams)));
  }
  if (state.selected >= 0) renderResult(false);
}

function renderDiary() {
  const data = loadDiary();
  const key = todayKey();
  const items = data.days[key] || [];
  els.goal.value = String(data.goal);
  els.diaryTitle.textContent = "Дневник · " + todayLabel();
  const totals = items.reduce((acc, item) => {
    acc.kcal += item.kcal;
    acc.protein += item.protein;
    acc.fat += item.fat;
    acc.carbs += item.carbs;
    return acc;
  }, { kcal: 0, protein: 0, fat: 0, carbs: 0 });
  const pct = data.goal > 0 ? Math.min(100, (totals.kcal / data.goal) * 100) : 0;
  els.goalFill.style.width = pct + "%";
  els.goalFill.parentElement.classList.toggle("over", totals.kcal > data.goal);
  if (totals.kcal <= data.goal) {
    els.goalText.textContent = fmt(totals.kcal, 0) + " из " + fmt(data.goal, 0) + " ккал · осталось " + fmt(data.goal - totals.kcal, 0);
  } else {
    els.goalText.textContent = fmt(totals.kcal, 0) + " из " + fmt(data.goal, 0) + " ккал · цель превышена на " + fmt(totals.kcal - data.goal, 0);
  }
  els.dayMacros.textContent = items.length
    ? "Белки " + fmt(totals.protein, 1) + " г · Жиры " + fmt(totals.fat, 1) + " г · Углеводы " + fmt(totals.carbs, 1) + " г"
    : "";
  els.diaryEmpty.hidden = items.length > 0;
  els.diaryList.replaceChildren();
  for (const item of items) {
    const li = document.createElement("li");
    const text = document.createElement("div");
    const title = document.createElement("b");
    title.textContent = item.nameRu;
    const meta = document.createElement("small");
    meta.textContent = fmt(item.grams, 0) + " г · " + fmt(item.kcal, 0) + " ккал";
    text.append(title, meta);
    const del = document.createElement("button");
    del.type = "button";
    del.className = "del";
    del.setAttribute("aria-label", "Удалить " + item.nameRu);
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

function addToDiary() {
  const food = foodOf(state.selected);
  if (!food) return;
  const data = loadDiary();
  const key = todayKey();
  const entry = {
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    label: food.label.id,
    nameRu: food.label.ru,
    nameEn: food.label.en,
    grams: currentGrams(),
    kcal: Number(els.resultCard.dataset.kcal),
    protein: Number(els.resultCard.dataset.protein),
    fat: Number(els.resultCard.dataset.fat),
    carbs: Number(els.resultCard.dataset.carbs),
    fiber: Number(els.resultCard.dataset.fiber),
    sugar: Number(els.resultCard.dataset.sugar),
    ts: Date.now(),
  };
  data.days[key] = data.days[key] || [];
  data.days[key].push(entry);
  saveDiary(data);
  renderDiary();
  burst(els.addBtn);
  els.addBtn.textContent = "В дневнике ✓";
  setTimeout(() => {
    els.addBtn.textContent = "Добавить в дневник";
  }, 900);
}

function openSearch() {
  renderSearch(els.searchInput.value || "");
  els.searchDialog.showModal();
  els.searchInput.focus();
}

function renderSearch(query) {
  const q = query.trim().toLocaleLowerCase("ru");
  els.searchList.replaceChildren();
  const rows = state.labels
    .map((label, index) => ({ label, index }))
    .filter(({ label }) => {
      if (!q) return true;
      return (label.ru + " " + label.en + " " + label.id.replaceAll("_", " ")).toLocaleLowerCase("ru").includes(q);
    })
    .sort((a, b) => a.label.ru.localeCompare(b.label.ru, "ru"));
  for (const row of rows) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "dish-pick";
    const title = document.createElement("b");
    title.textContent = row.label.ru;
    const sub = document.createElement("small");
    sub.textContent = row.label.en;
    btn.append(title, sub);
    btn.addEventListener("click", () => {
      selectDish(row.index, true, true);
      els.searchDialog.close();
    });
    els.searchList.appendChild(btn);
  }
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Такого блюда в списке из 101 нет.";
    els.searchList.appendChild(empty);
  }
}

async function fileToBitmap(file) {
  if (!file || !file.type.startsWith("image/")) {
    throw new Error("Этот файл не похож на фото. Подойдут JPG, PNG или WEBP.");
  }
  try {
    return await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    throw new Error("Не удалось прочитать фото. Попробуй другой снимок.");
  }
}

function showPhoto(file) {
  if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
  state.objectUrl = URL.createObjectURL(file);
  els.photo.src = state.objectUrl;
  els.photoCard.hidden = false;
  els.result.hidden = false;
  els.scanline.hidden = false;
  els.photoStatus.hidden = false;
  els.photoStatus.textContent = state.ready ? "Смотрим, что на тарелке…" : "Фото есть, ждём нейросеть…";
}

async function analyzeBitmap(bitmap) {
  if (!state.model) return;
  state.busy = true;
  els.scanline.hidden = false;
  els.photoStatus.hidden = false;
  const size = state.imgSize;
  const scores = tf.tidy(() => {
    const img = tf.browser.fromPixels(bitmap);
    const resized = tf.image.resizeBilinear(img, [size, size]).toFloat();
    const batched = resized.expandDims(0);
    let out = state.model.predict(batched);
    if (out && out.probs) out = out.probs;
    if (Array.isArray(out)) out = out[0];
    return out;
  });
  const data = await scores.data();
  scores.dispose();
  bitmap.close?.();
  state.probs = Float32Array.from(data);
  let best = 0;
  for (let i = 1; i < state.probs.length; i += 1) {
    if (state.probs[i] > state.probs[best]) best = i;
  }
  state.busy = false;
  els.scanline.hidden = true;
  els.photoStatus.hidden = true;
  selectDish(best, false, true);
}

async function handleFile(file) {
  setError("");
  let bitmap;
  try {
    bitmap = await fileToBitmap(file);
  } catch (err) {
    setError(err.message || "Не получилось открыть файл.");
    return;
  }
  showPhoto(file);
  if (!state.ready) {
    state.pending = bitmap;
    return;
  }
  try {
    await analyzeBitmap(bitmap);
  } catch (err) {
    console.error(err);
    els.scanline.hidden = true;
    els.photoStatus.hidden = true;
    setError("Не удалось распознать фото. Попробуй ещё раз или выбери блюдо вручную.");
  }
}

function stopCamera() {
  if (state.stream) {
    for (const track of state.stream.getTracks()) track.stop();
    state.stream = null;
  }
  els.video.srcObject = null;
}

async function openCamera() {
  els.camError.hidden = true;
  if (!navigator.mediaDevices?.getUserMedia) {
    els.shotFile.click();
    return;
  }
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: "environment" } },
    });
    els.video.srcObject = state.stream;
    els.cameraDialog.showModal();
  } catch (err) {
    const name = err && err.name;
    if (name === "NotAllowedError") {
      setError("Нет доступа к камере. Разреши камеру в браузере или выбери снимок из галереи.");
    } else if (name === "NotFoundError" || name === "OverconstrainedError") {
      setError("Камера не найдена. Можно выбрать готовое фото.");
    } else {
      setError("Камера не открылась. Выбери файл или снимок из галереи.");
    }
    els.shotFile.click();
  }
}

async function takePhoto() {
  const video = els.video;
  if (!video.videoWidth) {
    els.camError.hidden = false;
    els.camError.textContent = "Камера ещё не дала кадр. Подожди секунду.";
    return;
  }
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
  stopCamera();
  els.cameraDialog.close();
  if (!blob) {
    setError("Не удалось сохранить кадр.");
    return;
  }
  const file = new File([blob], "camera.jpg", { type: "image/jpeg" });
  await handleFile(file);
}

async function prepareBackend() {
  try {
    await tf.setBackend("webgl");
    await tf.ready();
  } catch {
    await tf.setBackend("cpu");
    await tf.ready();
  }
}

async function loadModel() {
  await prepareBackend();
  const metaUrl = new URL("model/metadata.json", document.baseURI);
  try {
    const res = await fetch(metaUrl);
    if (res.ok) state.meta = await res.json();
  } catch {
    state.meta = null;
  }
  if (state.meta?.img_size) state.imgSize = state.meta.img_size;
  const version = state.meta?.version || "v1";
  const idb = "indexeddb://caloric-scan-" + version;
  const modelUrl = new URL("model/model.json", document.baseURI).href;
  try {
    state.model = await tf.loadGraphModel(idb);
    setLoad(100, "Нейросеть взята из памяти браузера");
  } catch {
    state.model = await tf.loadGraphModel(modelUrl, {
      onProgress: (frac) => {
        const pct = Math.round(frac * 100);
        setLoad(pct, "Загружаем нейросеть… " + pct + "%");
      },
    });
    setLoad(100, "Сохраняем модель в браузере…");
    try {
      await state.model.save(idb);
    } catch (err) {
      console.warn("indexeddb", err);
    }
    setLoad(100, "Нейросеть готова — можно снимать");
  }
  const shape = state.model.inputs?.[0]?.shape;
  if (shape && shape.length >= 3 && shape[1] > 0) state.imgSize = shape[1];
  const warm = tf.zeros([1, state.imgSize, state.imgSize, 3]);
  const out = state.model.predict(warm);
  const tensor = out && out.probs ? out.probs : (Array.isArray(out) ? out[0] : out);
  if (tensor && tensor.data) await tensor.data();
  warm.dispose();
  tf.dispose(out);
  state.ready = true;
  setLoad(100, "Нейросеть готова — можно снимать");
  if (state.meta && state.meta.test_top1) {
    const top1 = fmt(state.meta.test_top1 * 100, 1);
    const top5 = fmt(state.meta.test_top5 * 100, 1);
    els.modelNote.textContent = "Модель " + (state.meta.backbone || "MobileNetV2")
      + " на официальном тесте Food-101 (" + state.meta.test_images + " фото): top-1 "
      + top1 + "%, top-5 " + top5 + "%.";
  }
  if (state.pending) {
    const pending = state.pending;
    state.pending = null;
    await analyzeBitmap(pending);
  }
}

async function boot() {
  applyTheme(localStorage.getItem(THEME_KEY) || "light");
  renderDiary();
  const [labels, nutrition] = await Promise.all([
    fetch(new URL("data/labels.json", document.baseURI)).then((r) => r.json()),
    fetch(new URL("data/nutrition.json", document.baseURI)).then((r) => r.json()),
  ]);
  state.labels = labels;
  state.nutrition = nutrition;
  renderSearch("");
  await loadModel();
}

els.themeBtn.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next);
});
els.pickBtn.addEventListener("click", () => els.file.click());
els.shotBtn.addEventListener("click", () => openCamera());
els.file.addEventListener("change", () => {
  const file = els.file.files && els.file.files[0];
  els.file.value = "";
  if (file) handleFile(file);
});
els.shotFile.addEventListener("change", () => {
  const file = els.shotFile.files && els.shotFile.files[0];
  els.shotFile.value = "";
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
    els.dropzone.classList.remove("drag");
  });
});
els.dropzone.addEventListener("drop", (event) => {
  const file = event.dataTransfer.files && event.dataTransfer.files[0];
  if (file) handleFile(file);
});
els.gramsRange.addEventListener("input", () => syncGrams(true));
els.gramsNum.addEventListener("input", () => syncGrams(false));
els.addBtn.addEventListener("click", addToDiary);
els.fixBtn.addEventListener("click", openSearch);
els.manualOpen.addEventListener("click", openSearch);
els.searchInput.addEventListener("input", () => renderSearch(els.searchInput.value));
els.goal.addEventListener("change", () => {
  const data = loadDiary();
  const goal = Number(els.goal.value);
  data.goal = Number.isFinite(goal) ? Math.min(8000, Math.max(800, Math.round(goal))) : 2000;
  els.goal.value = String(data.goal);
  saveDiary(data);
  renderDiary();
});
els.clearDay.addEventListener("click", () => {
  const data = loadDiary();
  data.days[todayKey()] = [];
  saveDiary(data);
  renderDiary();
});
els.shutter.addEventListener("click", () => takePhoto());
els.closeCam.addEventListener("click", () => {
  stopCamera();
  els.cameraDialog.close();
});
els.useFileCam.addEventListener("click", () => {
  stopCamera();
  els.cameraDialog.close();
  els.shotFile.click();
});
els.cameraDialog.addEventListener("close", stopCamera);

boot().catch((err) => {
  console.error(err);
  setLoad(0, "Не удалось загрузить модель");
  setError("Не удалось загрузить модель. Проверь интернет и обнови страницу. Блюдо всё равно можно выбрать из списка, но по фото — только с моделью.");
});
