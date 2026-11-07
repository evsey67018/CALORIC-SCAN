#!/usr/bin/env python3
"""Кодирует /tmp/mem_store в int8-чанки тем же PCA, что и первая модель.

Ждёт, пока train_dish_and_memory.py освободит CPU и запишет SavedModel.
После чанка сырые jpeg удаляются.
"""
from __future__ import annotations

import json
import os
import sqlite3
import struct
import time

os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")

import numpy as np

STORE = "/tmp/mem_store"
DB = "/tmp/mem_ledger.sqlite"
BANK = "/tmp/memory_bank"
SAVED = "/tmp/caloric_dish_v4_saved"
CHUNK = 2000
IMG = 224


def log(msg: str) -> None:
    print(msg, flush=True)


def dish_running() -> bool:
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\x00", b" ").decode()
        except OSError:
            continue
        if "train_dish_and_memory.py" in cmd:
            return True
    return False


def l2(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x, axis=-1, keepdims=True)
    return (x / np.maximum(n, 1e-8)).astype(np.float32)


def write_chunk(path: str, vecs: np.ndarray, ids: np.ndarray) -> None:
    vecs = l2(vecs)
    count, dim = vecs.shape
    scales = np.zeros(count, dtype=np.float32)
    quant = np.zeros((count, dim), dtype=np.int8)
    for i, row in enumerate(vecs):
        scale = max(float(np.max(np.abs(row))), 1e-8)
        scales[i] = scale
        quant[i] = np.clip(np.round(row / scale * 127), -127, 127)
    blob = bytearray()
    blob += b"CSM1"
    blob += struct.pack("<HHI", 1, dim, count)
    blob += quant.tobytes()
    blob += scales.astype(np.float16).tobytes()
    blob += ids.astype(np.uint16).tobytes()
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(blob)
    os.replace(tmp, path)


def load_index() -> dict:
    with open(os.path.join(BANK, "index.json"), encoding="utf-8") as fh:
        return json.load(fh)


def save_index(index: dict) -> None:
    path = os.path.join(BANK, "index.json")
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False)
    os.replace(tmp, path)


def list_pending(db: sqlite3.Connection, limit: int) -> list[tuple[str, str]]:
    rows = []
    for class_id in os.listdir(STORE):
        folder = os.path.join(STORE, class_id)
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if not name.endswith(".jpg"):
                continue
            path = os.path.join(folder, name)
            seen = db.execute("SELECT embedded FROM seen WHERE md5 LIKE ?", (name[:16] + "%",)).fetchone()
            # embedded column may not exist yet
            rows.append((class_id, path))
            if len(rows) >= limit:
                return rows
    return rows


def main() -> None:
    log("жду модель блюда и свободный CPU…")
    while dish_running() or not os.path.exists(os.path.join(SAVED, "saved_model.pb")):
        time.sleep(20)
    while not os.path.exists(os.path.join(BANK, "index.json")):
        time.sleep(10)
    import tensorflow as tf

    log("эмбеддер стартовал")
    model = tf.saved_model.load(SAVED)
    serve = model.signatures["serving_default"]
    db = sqlite3.connect(DB, timeout=60)
    cols = [r[1] for r in db.execute("PRAGMA table_info(seen)")]
    if "embedded" not in cols:
        db.execute("ALTER TABLE seen ADD COLUMN embedded INTEGER DEFAULT 0")
        db.commit()

    buf_v = []
    buf_i = []
    t0 = time.time()
    embedded = 0
    while True:
        index = load_index()
        id_of = {row["id"]: i for i, row in enumerate(index["classes"])}
        paths = []
        class_ids = []
        db.row_factory = sqlite3.Row
        for class_id in os.listdir(STORE):
            if class_id not in id_of:
                continue
            folder = os.path.join(STORE, class_id)
            if not os.path.isdir(folder):
                continue
            for name in os.listdir(folder):
                if not name.endswith(".jpg"):
                    continue
                path = os.path.join(folder, name)
                row = db.execute(
                    "SELECT embedded FROM seen WHERE md5 LIKE ? AND class_id=?",
                    (name.split(".")[0] + "%", class_id),
                ).fetchone()
                if row is not None and row["embedded"]:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    continue
                paths.append(path)
                class_ids.append(class_id)
                if len(paths) >= 48:
                    break
            if len(paths) >= 48:
                break
        if not paths:
            if buf_v:
                flush(index, buf_v, buf_i, id_of)
                buf_v, buf_i = [], []
            time.sleep(5)
            if time.time() - t0 > 20:
                log(f"эмбеддер ждёт файлы, уже {embedded}")
                t0 = time.time()
            continue
        used_paths = []
        used_ids = []
        tensors = []
        for path, class_id in zip(paths, class_ids):
            try:
                raw = tf.io.read_file(path)
                img = tf.io.decode_image(raw, channels=3, expand_animations=False)
                img = tf.image.resize(img, [IMG, IMG])
                tensors.append(img)
                used_paths.append(path)
                used_ids.append(class_id)
            except Exception:
                try:
                    os.remove(path)
                except OSError:
                    pass
        if not tensors:
            continue
        batch = tf.stack(tensors)
        try:
            out = serve(image=batch)
        except TypeError:
            out = serve(batch)
        key = "embedding" if "embedding" in out else list(out.keys())[-1]
        emb = l2(out[key].numpy())
        for vec, path, class_id in zip(emb, used_paths, used_ids):
            buf_v.append(vec)
            buf_i.append(id_of[class_id])
            db.execute(
                "UPDATE seen SET embedded=1 WHERE class_id=? AND md5 LIKE ?",
                (class_id, os.path.basename(path).split(".")[0] + "%"),
            )
            try:
                os.remove(path)
            except OSError:
                pass
            embedded += 1
        db.commit()
        if embedded % 200 == 0:
            log(f"эмбеддингов {embedded}")
        if len(buf_v) >= CHUNK:
            index = load_index()
            flush(index, buf_v, buf_i, id_of)
            buf_v, buf_i = [], []
            rate = embedded / max(time.time() - t0, 1) * 60
            log(f"чанк записан, эмбеддингов {embedded}, {rate:.0f} /мин")


def flush(index, buf_v, buf_i, id_of) -> None:
    n = len(index["chunks"])
    name = f"bank_{n}.bin"
    vecs = np.stack(buf_v).astype(np.float32)
    ids = np.asarray(buf_i, np.uint16)
    write_chunk(os.path.join(BANK, name), vecs, ids)
    index["chunks"].append({"file": name, "count": int(len(vecs))})
    index["count"] = int(sum(c["count"] for c in index["chunks"]))
    save_index(index)
    with open("/tmp/embed_progress.json", "w", encoding="utf-8") as fh:
        json.dump(
            {"chunks": len(index["chunks"]), "vectors": index["count"], "last": name},
            fh,
        )
    log(f"записан {name}  векторов в банке {index['count']}")


if __name__ == "__main__":
    main()
