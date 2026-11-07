#!/usr/bin/env python3
"""Голова блюда на 224px и компактный банк памяти.

Backbone — копия чекпоинта v1 (MobileNetV2 Food-101), без обучения с нуля.
Новая голова учится на официальном train Food-101. Картинки из
/tmp/memory_images (поиск и Википедия) в softmax не идут: из них считаются
прототипы kNN. Официальный test Food-101 в банк не попадает.
"""
from __future__ import annotations

import json
import math
import os
import struct
import sys

os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import numpy as np
import tensorflow as tf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import train as dishtrain
import train_seg as segv2

IMG = 224
FEAT = 1280
EXCLUDE = "/tmp/poke_holdout.jpg"


def log(msg: str) -> None:
    print(msg, flush=True)


def l2(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.maximum(n, 1e-8)


def extract(model, paths: list[str], batch: int = 48) -> np.ndarray:
    feats = np.zeros((len(paths), FEAT), dtype=np.float32)
    if not paths:
        return feats

    def load(path):
        raw = tf.io.read_file(path)
        img = tf.io.decode_image(raw, channels=3, expand_animations=False)
        img = tf.image.resize(img, [IMG, IMG])
        img.set_shape([IMG, IMG, 3])
        return img

    ds = tf.data.Dataset.from_tensor_slices(list(paths))
    ds = ds.map(load, num_parallel_calls=4, deterministic=True)
    ds = ds.batch(batch).prefetch(2)
    idx = 0
    t0 = tf.timestamp().numpy() if False else None
    import time
    t0 = time.time()
    for step, batch_img in enumerate(ds):
        pred = model(batch_img, training=False).numpy()
        b = pred.shape[0]
        feats[idx : idx + b] = pred
        idx += b
        if step % 40 == 0 or idx == len(paths):
            dt = max(time.time() - t0, 1e-6)
            log(f"  признаки {idx}/{len(paths)}  {idx / dt:.1f} img/s")
    if idx != len(paths):
        raise RuntimeError(f"извлечено {idx} из {len(paths)}")
    return feats


def train_heads(x_train, y_train, x_val, y_val):
    ytr = tf.keras.utils.to_categorical(y_train, 101)
    yva = tf.keras.utils.to_categorical(y_val, 101)
    results = []
    specs = {
        "linear": [tf.keras.layers.Dense(101, activation="softmax", name="probs")],
        "mlp": [
            tf.keras.layers.Dense(384, activation="relu", name="hidden"),
            tf.keras.layers.Dropout(0.35),
            tf.keras.layers.Dense(101, activation="softmax", name="probs"),
        ],
    }
    for name, layers in specs.items():
        inp = tf.keras.Input(shape=(FEAT,), name="features")
        h = inp
        for layer in layers:
            h = layer(h)
        head = tf.keras.Model(inp, h, name=name)
        head.compile(
            optimizer=tf.keras.optimizers.Adam(1e-3),
            loss=tf.keras.losses.CategoricalCrossentropy(label_smoothing=0.05),
            metrics=[tf.keras.metrics.CategoricalAccuracy(name="accuracy")],
        )
        head.fit(
            x_train,
            ytr,
            validation_data=(x_val, yva),
            epochs=24,
            batch_size=512,
            verbose=0,
            callbacks=[
                tf.keras.callbacks.EarlyStopping(
                    monitor="val_accuracy", mode="max", patience=4, restore_best_weights=True
                )
            ],
        )
        val = float(head.evaluate(x_val, yva, verbose=0)[1])
        log(f"голова {name}: val top1 {val:.4f}")
        results.append((val, name, head))
    results.sort(key=lambda row: row[0], reverse=True)
    return results[0]


def randomized_pca(x: np.ndarray, dim: int) -> tuple[np.ndarray, np.ndarray, float]:
    mean = x.mean(axis=0).astype(np.float32)
    xc = x - mean
    extra = 16
    rng = np.random.default_rng(0)
    q = rng.normal(size=(xc.shape[1], dim + extra)).astype(np.float32)
    for _ in range(4):
        q, _ = np.linalg.qr(xc.T @ (xc @ q))
    b = xc @ q
    _u, s, vt = np.linalg.svd(b, full_matrices=False)
    comp = (vt[:dim] @ q.T).astype(np.float32)
    proj_var = (s[:dim] ** 2).sum()
    total = float((s ** 2).sum()) + 1e-8
    return mean, comp, float(proj_var / total)


def project(x: np.ndarray, mean: np.ndarray, comp: np.ndarray) -> np.ndarray:
    y = (x - mean) @ comp.T
    return l2(y.astype(np.float32))


def kmeans(x: np.ndarray, k: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    k = min(k, len(x))
    cent = x[rng.choice(len(x), size=k, replace=False)].copy()
    for _ in range(8):
        lab = (x @ cent.T).argmax(axis=1)
        nxt = []
        for j in range(k):
            pts = x[lab == j]
            if len(pts) == 0:
                nxt.append(cent[j])
            else:
                nxt.append(l2(pts.mean(axis=0, keepdims=True))[0])
        cent = np.stack(nxt)
    return cent


def clean_class(vecs: np.ndarray, probs: np.ndarray) -> np.ndarray:
    if len(vecs) <= 1:
        if len(vecs) == 1 and float(probs.max()) < 0.04:
            return vecs[:0]
        return vecs
    keep = np.ones(len(vecs), dtype=bool)
    for _ in range(2):
        kept = vecs[keep]
        if len(kept) < 2:
            break
        cen = l2(kept.mean(axis=0, keepdims=True))[0]
        sim = vecs @ cen
        keep = keep & (sim >= 0.22)
    # Близкие дубли.
    idx = np.flatnonzero(keep)
    chosen = []
    for i in idx:
        if any(float(vecs[i] @ vecs[j]) > 0.985 for j in chosen):
            continue
        chosen.append(i)
    if not chosen:
        return vecs[:0]
    out_i = []
    cen = l2(vecs[chosen].mean(axis=0, keepdims=True))[0]
    for i in chosen:
        if float(probs[i].max()) < 0.04 and float(vecs[i] @ cen) < 0.30:
            continue
        out_i.append(i)
    if not out_i:
        return vecs[:0]
    return vecs[out_i]


def softmax_head(head, feats: np.ndarray) -> np.ndarray:
    return head.predict(feats, batch_size=1024, verbose=0)


def vote_neighbors(sims: np.ndarray, ids: np.ndarray, k: int, temp: float):
    """sims (n, m), ids (m,) -> list of (top list of (sim, cls))."""
    # top-k indices per row
    k = min(k, sims.shape[1])
    part = np.argpartition(sims, -k, axis=1)[:, -k:]
    out = []
    for i in range(sims.shape[0]):
        cols = part[i]
        order = cols[np.argsort(-sims[i, cols])]
        out.append([(float(sims[i, j]), int(ids[j])) for j in order])
    return out


def fuse_one(probs, neigh, params, classes):
    temp = params["temperature"]
    votes = {}
    sims = {}
    for sim, ci in neigh:
        w = math.exp(sim / temp)
        votes[ci] = votes.get(ci, 0.0) + w
        if sim > sims.get(ci, -1.0):
            sims[ci] = sim
    z = sum(votes.values()) or 1.0
    items = sorted(votes.items(), key=lambda kv: -kv[1])
    top_ci, top_w = items[0]
    second = items[1][1] / z if len(items) > 1 else 0.0
    margin = top_w / z - second
    top_sim = sims[top_ci]
    cls_best = int(np.argmax(probs))
    cls_p = float(probs[cls_best])
    mapped = classes[top_ci]["classifierIndex"]
    long_tail = mapped is None
    take = (
        top_sim >= params["simMin"]
        and margin >= params["margin"]
        and cls_p < params["clsGate"]
        and (long_tail or mapped != cls_best)
    )
    if take:
        return {"source": "memory", "classifierIndex": mapped, "classIndex": top_ci, "conf": top_sim}
    blend = params["blend"]
    mixed = probs.astype(np.float64) * (1.0 - blend)
    for ci, w in votes.items():
        j = classes[ci]["classifierIndex"]
        if j is not None:
            mixed[j] += blend * (w / z)
    mixed /= mixed.sum() + 1e-12
    j = int(np.argmax(mixed))
    return {"source": "blend", "classifierIndex": j, "classIndex": None, "conf": float(mixed[j])}


def food101_correct(pred, truth) -> bool:
    return pred["classifierIndex"] is not None and int(pred["classifierIndex"]) == int(truth)


def tune_fuse(probs, neigh, classes, truth) -> dict:
    grid_sim = [0.32, 0.40, 0.48, 0.56]
    grid_margin = [0.02, 0.05, 0.1]
    grid_gate = [0.35, 0.5, 0.7]
    grid_blend = [0.0, 0.25, 0.45]
    best = None
    for sim_min in grid_sim:
        for margin in grid_margin:
            for gate in grid_gate:
                for blend in grid_blend:
                    params = {
                        "k": 8,
                        "temperature": 0.07,
                        "simMin": sim_min,
                        "margin": margin,
                        "clsGate": gate,
                        "blend": blend,
                    }
                    hit = 0
                    for i in range(len(truth)):
                        pred = fuse_one(probs[i], neigh[i], params, classes)
                        hit += int(food101_correct(pred, truth[i]))
                    score = hit / len(truth)
                    # При равной точности берём более низкий порог сходства,
                    # чтобы редкие блюда ещё могли перебить неуверенный класс.
                    key = (score, -sim_min, -gate)
                    if best is None or key > best[0]:
                        best = (key, params, score)
    log(f"fuse на внутренней проверке Food-101: {best[2]:.4f}  {best[1]}")
    return best[1]


def write_bank(path: str, vecs: np.ndarray, ids: np.ndarray) -> None:
    vecs = l2(vecs.astype(np.float32))
    count, dim = vecs.shape
    scales = np.zeros(count, dtype=np.float32)
    quant = np.zeros((count, dim), dtype=np.int8)
    for i, row in enumerate(vecs):
        scale = float(np.max(np.abs(row)))
        scale = max(scale, 1e-8)
        scales[i] = scale
        quant[i] = np.clip(np.round(row / scale * 127), -127, 127).astype(np.int8)
    blob = bytearray()
    blob += b"CSM1"
    blob += struct.pack("<HHI", 1, dim, count)
    blob += quant.tobytes()
    blob += scales.astype(np.float16).tobytes()
    blob += ids.astype(np.uint16).tobytes()
    with open(path, "wb") as fh:
        fh.write(blob)


def main():
    status_path = "/tmp/memory_images/STATUS.json"
    catalog_path = "/tmp/memory_images/catalog.json"
    if not os.path.exists(status_path):
        raise SystemExit("Нет /tmp/memory_images/STATUS.json — сбор картинок ещё идёт")
    with open(status_path, encoding="utf-8") as fh:
        status = json.load(fh)
    with open(catalog_path, encoding="utf-8") as fh:
        catalog = json.load(fh)
    log("каталог " + json.dumps(status, ensure_ascii=False))

    tf.keras.utils.set_random_seed(42)
    tf.config.threading.set_intra_op_parallelism_threads(4)
    tf.config.threading.set_inter_op_parallelism_threads(1)

    data = "/tmp/food-extract/food-101"
    classes_txt = dishtrain.load_classes(data)
    dishtrain.assert_labels(os.path.join(ROOT, "data", "labels.json"), classes_txt)
    train_groups = dishtrain.read_groups(data, "train", classes_txt)
    test_groups = dishtrain.read_groups(data, "test", classes_txt)
    rng = np.random.RandomState(42)
    train_p, train_y, val_p, val_y = dishtrain.take_split(train_groups, classes_txt, rng, 0, 20)
    test_p, test_y = [], []
    for idx, name in enumerate(classes_txt):
        items = list(test_groups[name])
        test_p.extend(items)
        test_y.extend([idx] * len(items))
    log(f"food101 train {len(train_p)} val {len(val_p)} test {len(test_p)}")

    log("Backbone v1 → 224")
    _dish160, base160 = segv2.load_dish_and_backbone("/tmp/caloric_head.weights.h5")
    base = tf.keras.applications.MobileNetV2(input_shape=(IMG, IMG, 3), include_top=False, weights=None)
    segv2.copy_backbone(base160, base)
    del _dish160, base160
    base.trainable = False
    inp = tf.keras.Input(shape=(IMG, IMG, 3), name="image")
    x = tf.keras.layers.Rescaling(1.0 / 127.5, offset=-1.0, name="preprocess")(inp)
    gap = tf.keras.layers.GlobalAveragePooling2D(name="gap")(base(x, training=False))
    encoder = tf.keras.Model(inp, gap, name="encoder")

    log("Признаки Food-101…")
    x_train = extract(encoder, train_p)
    y_train = np.asarray(train_y, dtype=np.int32)
    x_val = extract(encoder, val_p)
    y_val = np.asarray(val_y, dtype=np.int32)

    val_acc, head_name, head = train_heads(x_train, y_train, x_val, y_val)
    log(f"выбрана голова {head_name} val {val_acc:.4f}")

    # Картинки памяти.
    mem_paths, mem_ids, mem_hold_paths, mem_hold_ids = [], [], [], []
    by_id = {row["id"]: row for row in catalog}
    for row in catalog:
        if row.get("classifierIndex") is not None:
            continue
        folder = os.path.join("/tmp/memory_images", row["id"])
        if not os.path.isdir(folder):
            continue
        files = sorted(
            os.path.join(folder, name) for name in os.listdir(folder) if name.endswith(".jpg")
        )
        if not files:
            continue
        if len(files) >= 10:
            cut = max(1, len(files) // 5)
            hold, keep = files[-cut:], files[:-cut]
        else:
            hold, keep = [], files
        mem_paths.extend(keep)
        mem_ids.extend([row["id"]] * len(keep))
        mem_hold_paths.extend(hold)
        mem_hold_ids.extend([row["id"]] * len(hold))
    log(f"память train-картинок {len(mem_paths)}  holdout {len(mem_hold_paths)}")
    x_mem = extract(encoder, mem_paths) if mem_paths else np.zeros((0, FEAT), np.float32)
    x_hold = extract(encoder, mem_hold_paths) if mem_hold_paths else np.zeros((0, FEAT), np.float32)

    # PCA по подвыборке train Food-101 и памяти.
    rng_pca = np.random.default_rng(1)
    take = rng_pca.choice(len(x_train), size=min(6000, len(x_train)), replace=False)
    sample = x_train[take]
    if len(x_mem):
        take_m = rng_pca.choice(len(x_mem), size=min(4000, len(x_mem)), replace=False)
        sample = np.concatenate([sample, x_mem[take_m]], axis=0)
    dim = 64
    mean, comp, explained = randomized_pca(sample, dim)
    if explained < 0.72:
        dim = 96
        mean, comp, explained = randomized_pca(sample, dim)
    log(f"PCA dim {dim}  объяснённая дисперсия выборки {explained:.3f}")

    ex_vec = None
    if os.path.exists(EXCLUDE):
        ex_vec = project(extract(encoder, [EXCLUDE]), mean, comp)[0]

    def near_test(vecs: np.ndarray) -> np.ndarray:
        if ex_vec is None or len(vecs) == 0:
            return np.zeros(len(vecs), dtype=bool)
        return (vecs @ ex_vec) > 0.96

    z_train = project(x_train, mean, comp)
    z_val = project(x_val, mean, comp)
    z_mem = project(x_mem, mean, comp) if len(x_mem) else z_train[:0]
    z_hold = project(x_hold, mean, comp) if len(x_hold) else z_train[:0]
    p_mem = softmax_head(head, x_mem) if len(x_mem) else np.zeros((0, 101), np.float32)
    p_hold = softmax_head(head, x_hold) if len(x_hold) else np.zeros((0, 101), np.float32)

    # Классы банка: сначала 101 класс Food-101 в прежнем порядке, затем остальные.
    shipped = []
    id_to_index = {}
    for idx, name in enumerate(classes_txt):
        row = by_id.get(name, {})
        shipped.append({
            "id": name,
            "ru": row.get("ru") or name,
            "en": row.get("en") or name,
            "kcal": row.get("kcal", 0),
            "protein": row.get("protein", 0),
            "fat": row.get("fat", 0),
            "carbs": row.get("carbs", 0),
            "nutrition": row.get("nutrition", "usda"),
            "classifierIndex": idx,
        })
        id_to_index[name] = idx

    # Прототипы Food-101: 32 фото train на класс, 4 центра.
    proto_vecs = []
    proto_ids = []
    rng_img = np.random.default_rng(2)
    by_class = {i: [] for i in range(101)}
    for i, lab in enumerate(y_train):
        by_class[int(lab)].append(i)
    for lab, idxs in by_class.items():
        idxs = list(idxs)
        rng_img.shuffle(idxs)
        idxs = idxs[:32]
        vecs = z_train[idxs]
        cents = kmeans(vecs, 4, seed=1000 + lab)
        for c in cents:
            proto_vecs.append(c)
            proto_ids.append(id_to_index[classes_txt[lab]])

    # Прототипы длинного хвоста.
    grouped = {}
    for vec, dish_id, prob in zip(z_mem, mem_ids, p_mem):
        grouped.setdefault(dish_id, []).append((vec, prob))
    dropped_near = 0
    kept_classes = 0
    kept_images = 0
    ex_mask_cache = None
    for dish_id, pairs in grouped.items():
        vecs = np.stack([p[0] for p in pairs])
        probs = np.stack([p[1] for p in pairs])
        near = near_test(vecs)
        dropped_near += int(near.sum())
        vecs, probs = vecs[~near], probs[~near]
        if len(vecs) == 0:
            continue
        cleaned = clean_class(vecs, probs)
        if len(cleaned) == 0:
            continue
        if dish_id not in id_to_index:
            row = by_id[dish_id]
            id_to_index[dish_id] = len(shipped)
            shipped.append({
                "id": dish_id,
                "ru": row.get("ru") or row.get("en") or dish_id,
                "en": row.get("en") or dish_id,
                "kcal": row.get("kcal", 0),
                "protein": row.get("protein", 0),
                "fat": row.get("fat", 0),
                "carbs": row.get("carbs", 0),
                "nutrition": row.get("nutrition", "estimated"),
                "classifierIndex": None,
            })
        k = 4 if len(cleaned) >= 12 else 1
        cents = kmeans(cleaned, k, seed=abs(hash(dish_id)) % 100000)
        for c in cents:
            proto_vecs.append(c)
            proto_ids.append(id_to_index[dish_id])
        kept_classes += 1
        kept_images += len(cleaned)
    log(f"хвост классов {kept_classes}  фото после чистки {kept_images}  близких к тесту {dropped_near}")

    bank = l2(np.stack(proto_vecs).astype(np.float32))
    bank_ids = np.asarray(proto_ids, dtype=np.uint16)
    log(f"прототипов {len(bank)}  классов в индексе {len(shipped)}")

    # Holdout длинного хвоста.
    hold_hit = 0
    hold_n = 0
    if len(z_hold):
        near = near_test(z_hold)
        hold_neigh = []
        for start in range(0, len(z_hold), 512):
            sims = z_hold[start : start + 512] @ bank.T
            hold_neigh.extend(vote_neighbors(sims, bank_ids, 8, 0.07))
        for i, dish_id in enumerate(mem_hold_ids):
            if near[i] or dish_id not in id_to_index:
                continue
            votes = {}
            for sim, ci in hold_neigh[i]:
                votes[ci] = votes.get(ci, 0.0) + math.exp(sim / 0.07)
            winner = max(votes, key=votes.get)
            hold_n += 1
            hold_hit += int(winner == id_to_index[dish_id])
    hold_acc = hold_hit / hold_n if hold_n else None
    log(f"holdout памяти {hold_n}  top1 прототипа {hold_acc}")

    log("Признаки официального test…")
    x_test = extract(encoder, test_p)
    y_test = np.asarray(test_y, dtype=np.int32)
    p_test = softmax_head(head, x_test)
    p_val = softmax_head(head, x_val)
    z_test = project(x_test, mean, comp)

    def pack_metrics(probs, emb, truth):
        cls = probs.argmax(axis=1)
        top1 = float(np.mean(cls == truth))
        top5 = np.argpartition(probs, -5, axis=1)[:, -5:]
        top5_acc = float(np.mean(np.any(top5 == truth[:, None], axis=1)))
        neigh = []
        for start in range(0, len(emb), 512):
            sims = emb[start : start + 512] @ bank.T
            neigh.extend(vote_neighbors(sims, bank_ids, 8, 0.07))
        return top1, top5_acc, neigh

    val_top1, _val_top5, val_neigh = pack_metrics(p_val, z_val, y_val)
    params = tune_fuse(p_val, val_neigh, shipped, y_val)
    # Пересчитать соседей, если температура вдруг другая — она фиксирована 0.07.
    test_top1, test_top5, test_neigh = pack_metrics(p_test, z_test, y_test)
    fuse_hit = 0
    knn_hit = 0
    for i, truth in enumerate(y_test):
        pred = fuse_one(p_test[i], test_neigh[i], params, shipped)
        fuse_hit += int(food101_correct(pred, truth))
        # kNN: победитель голосования, даже если это длинный хвост (тогда ошибка).
        votes = {}
        for sim, ci in test_neigh[i]:
            votes[ci] = votes.get(ci, 0.0) + math.exp(sim / params["temperature"])
        winner = max(votes, key=votes.get)
        mapped = shipped[winner]["classifierIndex"]
        knn_hit += int(mapped is not None and int(mapped) == int(truth))
    fuse_top1 = fuse_hit / len(y_test)
    knn_top1 = knn_hit / len(y_test)
    log(f"TEST classifier {test_top1:.4f} top5 {test_top5:.4f} knn {knn_top1:.4f} fuse {fuse_top1:.4f}")

    # Сборка сети с PCA внутри embedding.
    image = tf.keras.Input(shape=(IMG, IMG, 3), name="image")
    h = tf.keras.layers.Rescaling(1.0 / 127.5, offset=-1.0, name="preprocess")(image)
    h = base(h, training=False)
    gap_l = tf.keras.layers.GlobalAveragePooling2D(name="gap")(h)
    if head_name == "mlp":
        hidden_w = head.get_layer("hidden").get_weights()
        probs_w = head.get_layer("probs").get_weights()
        hid = tf.keras.layers.Dense(384, activation="relu", name="hidden")(gap_l)
        probs = tf.keras.layers.Dense(101, activation="softmax", name="probs")(hid)
    else:
        probs_w = head.get_layer("probs").get_weights()
        hidden_w = None
        probs = tf.keras.layers.Dense(101, activation="softmax", name="probs")(gap_l)
    # y = (x - mean) @ comp.T = x @ W + b, W = comp.T, b = -mean @ W
    w = comp.T.astype(np.float32)
    b = (-mean @ w).astype(np.float32)
    emb = tf.keras.layers.Dense(dim, name="embedding")(gap_l)
    model = tf.keras.Model(image, [probs, emb], name="caloric_dish_v4")
    if hidden_w is not None:
        model.get_layer("hidden").set_weights(hidden_w)
    model.get_layer("probs").set_weights(probs_w)
    model.get_layer("embedding").set_weights([w, b])

    sample_paths = val_p[:6]
    batch = []
    for p in sample_paths:
        raw = tf.io.decode_jpeg(tf.io.read_file(p), channels=3)
        batch.append(tf.image.resize(raw, [IMG, IMG]).numpy())
    k_probs, k_emb = model.predict(np.stack(batch).astype(np.float32), verbose=0)
    n_probs = softmax_head(head, x_val[:6])
    n_emb = project(x_val[:6], mean, comp)
    log(f"сверка probs {np.max(np.abs(k_probs - n_probs)):.5f}  emb {np.max(np.abs(l2(k_emb) - n_emb)):.5f}")

    saved = "/tmp/caloric_dish_v4_saved"
    if os.path.isdir(saved):
        import shutil
        shutil.rmtree(saved)

    @tf.function(input_signature=[tf.TensorSpec([None, IMG, IMG, 3], tf.float32, name="image")])
    def serve(image_t):
        pr, em = model(image_t, training=False)
        return {"probs": pr, "embedding": em}

    tf.saved_model.save(model, saved, signatures={"serving_default": serve})
    export_dir = "/tmp/dish_v4_tfjs"
    os.makedirs(export_dir, exist_ok=True)
    for name in os.listdir(export_dir):
        if name == "model.json" or name.endswith(".bin"):
            os.remove(os.path.join(export_dir, name))
    cmd = [
        sys.executable,
        os.path.join(ROOT, "training", "tfjs_convert.py"),
        saved,
        export_dir,
        "--input_format", "tf_saved_model",
        "--output_format", "tfjs_graph_model",
        "--signature_name", "serving_default",
        "--saved_model_tags", "serve",
        "--quantize_float16",
        "--weight_shard_size_bytes", "4194304",
    ]
    log("Конвертация блюда…")
    import subprocess
    subprocess.check_call(cmd)

    bank_dir = "/tmp/memory_bank"
    os.makedirs(bank_dir, exist_ok=True)
    chunk = 8000
    chunks = []
    for start in range(0, len(bank), chunk):
        part = bank[start : start + chunk]
        part_ids = bank_ids[start : start + chunk]
        fname = f"bank_{len(chunks)}.bin"
        write_bank(os.path.join(bank_dir, fname), part, part_ids)
        chunks.append({"file": fname, "count": int(len(part))})
    index = {
        "version": "v4",
        "dim": dim,
        "count": int(len(bank)),
        "chunks": chunks,
        "fuse": params,
        "pcaExplained": round(explained, 4),
        "classes": shipped,
    }
    with open(os.path.join(bank_dir, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False)
    bank_bytes = sum(
        os.path.getsize(os.path.join(bank_dir, c["file"])) for c in chunks
    ) + os.path.getsize(os.path.join(bank_dir, "index.json"))
    dish_bytes = sum(
        os.path.getsize(os.path.join(export_dir, n))
        for n in os.listdir(export_dir)
        if n == "model.json" or n.endswith(".bin")
    )
    metrics = {
        "before_test_top1": 0.6106,
        "before_test_top5": 0.8546,
        "img_size": IMG,
        "head": head_name,
        "val_top1_classifier": round(float(val_top1), 4),
        "test_top1_classifier": round(float(test_top1), 4),
        "test_top5_classifier": round(float(test_top5), 4),
        "test_top1_knn": round(float(knn_top1), 4),
        "test_top1_fuse": round(float(fuse_top1), 4),
        "test_images": int(len(y_test)),
        "memory_holdout_images": hold_n,
        "memory_holdout_top1": None if hold_acc is None else round(float(hold_acc), 4),
        "catalog_dishes": status.get("catalog"),
        "classes_with_images": status.get("classes_with_images"),
        "downloaded_images": status.get("images"),
        "collect_stop": status.get("stop"),
        "prototypes": int(len(bank)),
        "index_classes": len(shipped),
        "tail_classes": kept_classes,
        "tail_images_kept": kept_images,
        "pca_dim": dim,
        "pca_explained": round(explained, 4),
        "bank_bytes": bank_bytes,
        "dish_bytes": dish_bytes,
        "fuse": params,
        "note": (
            "test — официальный test Food-101, не использовался ни для головы, ни для банка, "
            "ни для подбора fuse (fuse подобран на 20 фото/класс из train). "
            "holdout памяти — 20% фото длинного хвоста, отложенные до прототипов; "
            "подписи там равны поисковому запросу или названию статьи, это шумные метки."
        ),
    }
    with open("/tmp/v4_dish_metrics.json", "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, ensure_ascii=False, indent=2)
    log(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
