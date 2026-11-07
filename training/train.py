#!/usr/bin/env python3
"""Дообучение MobileNetV2 (ImageNet) на Food-101 и экспорт в TFJS.

Датасет в репозиторий не кладётся. Скачайте архив ETH Zürich и распакуйте его
(см. README). Пример:

  python training/train.py --data /tmp/food-extract/food-101 --out model
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time

os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
os.environ.setdefault("TF_NUM_INTRAOP_THREADS", "4")
os.environ.setdefault("TF_NUM_INTEROP_THREADS", "1")

import numpy as np
import tensorflow as tf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NUM_CLASSES = 101


def log(msg: str) -> None:
    print(msg, flush=True)


def load_classes(data_dir: str) -> list[str]:
    path = os.path.join(data_dir, "meta", "classes.txt")
    with open(path, encoding="utf-8") as fh:
        classes = [line.strip() for line in fh if line.strip()]
    if len(classes) != NUM_CLASSES:
        raise SystemExit(f"Ожидалось {NUM_CLASSES} классов, в classes.txt {len(classes)}")
    return classes


def assert_labels(labels_path: str, classes: list[str]) -> list[dict]:
    with open(labels_path, encoding="utf-8") as fh:
        labels = json.load(fh)
    ids = [row["id"] for row in labels]
    if ids != classes:
        raise SystemExit(
            "Порядок data/labels.json не совпадает с meta/classes.txt.\n"
            f"labels: {ids[:3]} ...\nclasses: {classes[:3]} ..."
        )
    return labels


def read_groups(data_dir: str, split: str, classes: list[str]) -> dict[str, list[str]]:
    groups = {name: [] for name in classes}
    list_path = os.path.join(data_dir, "meta", f"{split}.txt")
    img_root = os.path.join(data_dir, "images")
    with open(list_path, encoding="utf-8") as fh:
        for line in fh:
            rel = line.strip()
            if not rel:
                continue
            cls, _name = rel.split("/")
            groups[cls].append(os.path.join(img_root, rel + ".jpg"))
    return groups


def take_split(groups, classes, rng, limit: int, holdout: int):
    train_p, train_y, val_p, val_y = [], [], [], []
    for idx, cls in enumerate(classes):
        items = list(groups[cls])
        rng.shuffle(items)
        ho = items[:holdout] if holdout else []
        rest = items[holdout:]
        if limit:
            rest = rest[:limit]
        train_p.extend(rest)
        train_y.extend([idx] * len(rest))
        val_p.extend(ho)
        val_y.extend([idx] * len(ho))
    return train_p, train_y, val_p, val_y


def take_test(groups, classes, rng, limit: int):
    paths, labels = [], []
    for idx, cls in enumerate(classes):
        items = list(groups[cls])
        rng.shuffle(items)
        if limit:
            items = items[:limit]
        paths.extend(items)
        labels.extend([idx] * len(items))
    return paths, labels


def make_image_ds(paths, img_size: int, batch: int, training: bool):
    def load(path):
        raw = tf.io.read_file(path)
        img = tf.io.decode_jpeg(raw, channels=3, dct_method="INTEGER_FAST")
        img = tf.image.resize(img, [img_size, img_size])
        img.set_shape([img_size, img_size, 3])
        if training:
            img = tf.image.random_flip_left_right(img)
            img = tf.image.random_brightness(img, 24.0)
            img = tf.image.random_contrast(img, 0.85, 1.2)
            img = tf.image.random_saturation(img, 0.85, 1.2)
            img = tf.clip_by_value(img, 0.0, 255.0)
        return img

    ds = tf.data.Dataset.from_tensor_slices(list(paths))
    if training:
        ds = ds.shuffle(min(4096, max(1, len(paths))), reshuffle_each_iteration=True)
    ds = ds.map(load, num_parallel_calls=4, deterministic=not training)
    ds = ds.batch(batch).prefetch(2)
    return ds


def build_feature_extractor(img_size: int):
    base = tf.keras.applications.MobileNetV2(
        input_shape=(img_size, img_size, 3),
        include_top=False,
        weights="imagenet",
        pooling=None,
    )
    base.trainable = False
    inputs = tf.keras.Input(shape=(img_size, img_size, 3), name="image")
    x = tf.keras.layers.Rescaling(1.0 / 127.5, offset=-1.0, name="preprocess")(inputs)
    x = base(x, training=False)
    x = tf.keras.layers.GlobalAveragePooling2D(name="gap")(x)
    feat = tf.keras.Model(inputs, x, name="food_features")
    return base, feat


def extract_features(feat_model, paths, img_size: int, batch: int):
    n = len(paths)
    feat_dim = int(feat_model.output_shape[-1])
    feats = np.zeros((n, feat_dim), dtype=np.float32)
    ds = make_image_ds(paths, img_size, batch, training=False)
    idx = 0
    t0 = time.time()
    for step, batch_img in enumerate(ds):
        pred = feat_model(batch_img, training=False).numpy()
        b = pred.shape[0]
        feats[idx : idx + b] = pred
        idx += b
        if step % 25 == 0 or idx == n:
            dt = max(time.time() - t0, 1e-6)
            log(f"  признаки {idx}/{n}  {idx / dt:.1f} img/s")
    if idx != n:
        raise RuntimeError(f"Извлечено {idx} признаков из {n}")
    return feats


def train_head(x_train, y_train, x_val, y_val, epochs: int):
    feat_dim = x_train.shape[1]
    inp = tf.keras.Input(shape=(feat_dim,), name="features")
    x = tf.keras.layers.Dropout(0.3)(inp)
    out = tf.keras.layers.Dense(
        NUM_CLASSES,
        activation="softmax",
        kernel_regularizer=tf.keras.regularizers.l2(1e-4),
        name="probs",
    )(x)
    head = tf.keras.Model(inp, out, name="food_head")
    ytr = tf.keras.utils.to_categorical(y_train, NUM_CLASSES)
    yva = tf.keras.utils.to_categorical(y_val, NUM_CLASSES)
    head.compile(
        optimizer=tf.keras.optimizers.Adam(1e-3),
        loss=tf.keras.losses.CategoricalCrossentropy(label_smoothing=0.05),
        metrics=[
            tf.keras.metrics.CategoricalAccuracy(name="accuracy"),
            tf.keras.metrics.TopKCategoricalAccuracy(k=5, name="top5"),
        ],
    )
    callbacks = [
        tf.keras.callbacks.EarlyStopping(
            monitor="val_accuracy",
            mode="max",
            patience=4,
            restore_best_weights=True,
        ),
        tf.keras.callbacks.ReduceLROnPlateau(
            monitor="val_accuracy", mode="max", factor=0.5, patience=2, min_lr=1e-5
        ),
    ]
    history = head.fit(
        x_train,
        ytr,
        validation_data=(x_val, yva),
        epochs=epochs,
        batch_size=512,
        callbacks=callbacks,
        verbose=2,
    )
    return head, history.history


def assemble_model(base, head, img_size: int):
    inputs = tf.keras.Input(shape=(img_size, img_size, 3), name="image")
    x = tf.keras.layers.Rescaling(1.0 / 127.5, offset=-1.0, name="preprocess")(inputs)
    x = base(x, training=False)
    x = tf.keras.layers.GlobalAveragePooling2D(name="gap")(x)
    x = tf.keras.layers.Dropout(0.3, name="drop")(x)
    out = tf.keras.layers.Dense(
        NUM_CLASSES,
        activation="softmax",
        kernel_regularizer=tf.keras.regularizers.l2(1e-4),
        name="probs",
    )(x)
    model = tf.keras.Model(inputs, out, name="caloric_scan")
    model.get_layer("probs").set_weights(head.get_layer("probs").get_weights())
    return model


def unfreeze_top(base) -> int:
    """Разморозить последние блоки MobileNetV2, BatchNorm оставить выключенным."""
    base.trainable = True
    opened = 0
    for layer in base.layers:
        name = layer.name
        is_top = name.startswith("block_15") or name.startswith("block_16") or name.startswith("Conv_1")
        if isinstance(layer, tf.keras.layers.BatchNormalization) or not is_top:
            layer.trainable = False
        else:
            layer.trainable = True
            opened += 1
    return opened


def evaluate(model, paths, labels, img_size: int, batch: int):
    y = np.asarray(labels, dtype=np.int32)
    correct1 = 0
    correct5 = 0
    per_ok = np.zeros(NUM_CLASSES, dtype=np.int64)
    per_n = np.zeros(NUM_CLASSES, dtype=np.int64)
    ds = make_image_ds(paths, img_size, batch, training=False)
    idx = 0
    t0 = time.time()
    n = len(paths)
    for step, batch_img in enumerate(ds):
        prob = model(batch_img, training=False).numpy()
        b = prob.shape[0]
        yt = y[idx : idx + b]
        pred = prob.argmax(axis=1)
        correct1 += int(np.sum(pred == yt))
        top5 = np.argpartition(prob, -5, axis=1)[:, -5:]
        correct5 += int(np.sum(np.any(top5 == yt[:, None], axis=1)))
        for truth, guess in zip(yt, pred):
            per_n[truth] += 1
            per_ok[truth] += int(truth == guess)
        idx += b
        if step % 25 == 0 or idx == n:
            dt = max(time.time() - t0, 1e-6)
            log(f"  оценка {idx}/{n}  {idx / dt:.1f} img/s")
    if idx != n:
        raise RuntimeError(f"Оценено {idx} из {n}")
    return correct1 / n, correct5 / n, per_ok, per_n


def export_tfjs(model, out_dir: str, img_size: int) -> dict:
    saved = "/tmp/caloric_scan_saved"
    if os.path.isdir(saved):
        shutil.rmtree(saved)
    os.makedirs(out_dir, exist_ok=True)

    @tf.function(input_signature=[tf.TensorSpec([None, img_size, img_size, 3], tf.float32, name="image")])
    def serve(image):
        return model(image, training=False)

    tf.saved_model.save(model, saved, signatures={"serving_default": serve})
    if os.path.isdir(out_dir):
        for name in os.listdir(out_dir):
            path = os.path.join(out_dir, name)
            if name.startswith("group") or name == "model.json":
                os.remove(path)
    # Пути — позиционные и идут первыми: у --quantize_float16 nargs='?'
    # и он иначе съедает следующий путь.
    cmd = [
        sys.executable,
        os.path.join(ROOT, "training", "tfjs_convert.py"),
        saved,
        out_dir,
        "--input_format",
        "tf_saved_model",
        "--output_format",
        "tfjs_graph_model",
        "--signature_name",
        "serving_default",
        "--saved_model_tags",
        "serve",
        "--quantize_float16",
        "--weight_shard_size_bytes",
        "4194304",
    ]
    log("Конвертация TFJS: " + " ".join(cmd))
    subprocess.check_call(cmd)
    total = 0
    files = []
    for name in sorted(os.listdir(out_dir)):
        path = os.path.join(out_dir, name)
        if os.path.isfile(path) and (name == "model.json" or name.endswith(".bin")):
            size = os.path.getsize(path)
            total += size
            files.append({"file": name, "bytes": size})
    return {"files": files, "bytes": total, "quantize": "float16"}


def main():
    parser = argparse.ArgumentParser(description="Fine-tune MobileNetV2 on Food-101")
    parser.add_argument("--data", required=True, help="Каталог food-101 с meta/ и images/")
    parser.add_argument("--out", default=os.path.join(ROOT, "model"))
    parser.add_argument("--labels", default=os.path.join(ROOT, "data", "labels.json"))
    parser.add_argument("--metrics", default=os.path.join(ROOT, "training", "metrics.json"))
    parser.add_argument("--img-size", type=int, default=160)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--limit-per-class", type=int, default=0, help="0 = весь train, кроме holdout")
    parser.add_argument("--holdout-per-class", type=int, default=20)
    parser.add_argument("--test-per-class", type=int, default=0, help="0 = весь официальный test")
    parser.add_argument("--head-epochs", type=int, default=30)
    parser.add_argument("--finetune-epochs", type=int, default=1)
    parser.add_argument("--finetune-per-class", type=int, default=0, help="0 = все train-фото")
    parser.add_argument("--finetune-lr", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--skip-convert", action="store_true")
    args = parser.parse_args()

    tf.keras.utils.set_random_seed(args.seed)
    tf.config.threading.set_intra_op_parallelism_threads(4)
    tf.config.threading.set_inter_op_parallelism_threads(1)

    classes = load_classes(args.data)
    assert_labels(args.labels, classes)
    rng = np.random.RandomState(args.seed)

    log("Читаю списки Food-101…")
    train_groups = read_groups(args.data, "train", classes)
    test_groups = read_groups(args.data, "test", classes)
    train_p, train_y, val_p, val_y = take_split(
        train_groups, classes, rng, args.limit_per_class, args.holdout_per_class
    )
    test_p, test_y = take_test(test_groups, classes, np.random.RandomState(args.seed + 1), args.test_per_class)
    for cls in classes:
        if len(train_groups[cls]) < 700 or len(test_groups[cls]) < 200:
            raise SystemExit(
                f"Неполный датасет у класса {cls}: "
                f"train {len(train_groups[cls])} test {len(test_groups[cls])}"
            )
    log(f"train {len(train_p)}  val {len(val_p)}  test {len(test_p)}  img {args.img_size}")
    if not os.path.exists(train_p[0]):
        raise SystemExit(f"Нет файла {train_p[0]}")

    t_all = time.time()
    log("Строю MobileNetV2 и качаю веса ImageNet…")
    base, feat_model = build_feature_extractor(args.img_size)

    log("Признаки train…")
    t0 = time.time()
    x_train = extract_features(feat_model, train_p, args.img_size, args.batch)
    y_train = np.asarray(train_y, dtype=np.int32)
    train_feat_sec = time.time() - t0

    log("Признаки val…")
    x_val = extract_features(feat_model, val_p, args.img_size, args.batch)
    y_val = np.asarray(val_y, dtype=np.int32)

    log("Обучаю голову классификатора…")
    head, head_hist = train_head(x_train, y_train, x_val, y_val, args.head_epochs)
    model = assemble_model(base, head, args.img_size)

    log("Сверка головы и полной модели на val…")
    val_top1, val_top5, _, _ = evaluate(model, val_p, val_y, args.img_size, args.batch)
    log(f"val после головы: top1 {val_top1:.4f}  top5 {val_top5:.4f}")

    finetune_info = {"epochs": 0, "kept": "head"}
    head_val_top1 = val_top1
    if args.finetune_epochs > 0:
        opened = unfreeze_top(base)
        if opened == 0:
            names = [layer.name for layer in base.layers]
            raise SystemExit("Не нашёл верхние блоки для дообучения. Слои: " + ", ".join(names[-12:]))
        model.compile(
            optimizer=tf.keras.optimizers.Adam(args.finetune_lr),
            loss=tf.keras.losses.CategoricalCrossentropy(label_smoothing=0.05),
            metrics=[
                tf.keras.metrics.CategoricalAccuracy(name="accuracy"),
                tf.keras.metrics.TopKCategoricalAccuracy(k=5, name="top5"),
            ],
        )
        pre_path = "/tmp/caloric_head.weights.h5"
        model.save_weights(pre_path)
        probe = next(layer for layer in base.layers if layer.trainable and layer.weights)
        probe_before = float(np.sum(np.abs(probe.get_weights()[0])))
        ft_paths = train_p
        ft_labels = train_y
        if args.finetune_per_class:
            rng_ft = np.random.RandomState(args.seed + 7)
            chosen_p, chosen_y = [], []
            by_class = {i: [] for i in range(NUM_CLASSES)}
            for path, lab in zip(train_p, train_y):
                by_class[lab].append(path)
            for lab, paths in by_class.items():
                paths = list(paths)
                rng_ft.shuffle(paths)
                paths = paths[: args.finetune_per_class]
                chosen_p.extend(paths)
                chosen_y.extend([lab] * len(paths))
            ft_paths, ft_labels = chosen_p, chosen_y
        log(f"Дообучение последних блоков: слоёв {opened}, фото {len(ft_paths)}, эпох {args.finetune_epochs}")

        # Отдельный конвейер: пути и метки вместе, аугментация включена.
        def load_pair(path, lab):
            raw = tf.io.read_file(path)
            img = tf.io.decode_jpeg(raw, channels=3, dct_method="INTEGER_FAST")
            img = tf.image.resize(img, [args.img_size, args.img_size])
            img.set_shape([args.img_size, args.img_size, 3])
            img = tf.image.random_flip_left_right(img)
            img = tf.image.random_brightness(img, 24.0)
            img = tf.image.random_contrast(img, 0.85, 1.2)
            img = tf.image.random_saturation(img, 0.85, 1.2)
            img = tf.clip_by_value(img, 0.0, 255.0)
            return img, tf.one_hot(tf.cast(lab, tf.int32), NUM_CLASSES)

        ft_ds = tf.data.Dataset.from_tensor_slices((list(ft_paths), list(ft_labels)))
        ft_ds = ft_ds.shuffle(min(4096, len(ft_paths)), reshuffle_each_iteration=True)
        ft_ds = ft_ds.map(load_pair, num_parallel_calls=4, deterministic=False)
        ft_ds = ft_ds.batch(args.batch).prefetch(2)

        def load_val(path, lab):
            raw = tf.io.read_file(path)
            img = tf.io.decode_jpeg(raw, channels=3, dct_method="INTEGER_FAST")
            img = tf.image.resize(img, [args.img_size, args.img_size])
            img.set_shape([args.img_size, args.img_size, 3])
            return img, tf.one_hot(tf.cast(lab, tf.int32), NUM_CLASSES)

        val_ds = tf.data.Dataset.from_tensor_slices((list(val_p), list(val_y)))
        val_ds = val_ds.map(load_val, num_parallel_calls=4, deterministic=True)
        val_ds = val_ds.batch(args.batch).prefetch(2)

        t_ft = time.time()
        ckpt = "/tmp/caloric_ft.weights.h5"
        model.fit(
            ft_ds,
            validation_data=val_ds,
            epochs=args.finetune_epochs,
            verbose=2,
            callbacks=[
                tf.keras.callbacks.ModelCheckpoint(
                    ckpt,
                    monitor="val_accuracy",
                    mode="max",
                    save_best_only=True,
                    save_weights_only=True,
                )
            ],
        )
        if os.path.exists(ckpt):
            model.load_weights(ckpt)
        ft_sec = time.time() - t_ft
        probe_after = float(np.sum(np.abs(probe.get_weights()[0])))
        log(f"сдвиг весов {probe.name}: {abs(probe_after - probe_before):.6f}")
        ft_top1, ft_top5, _, _ = evaluate(model, val_p, val_y, args.img_size, args.batch)
        log(f"val после дообучения: top1 {ft_top1:.4f}  top5 {ft_top5:.4f}")
        kept = "finetuned"
        if ft_top1 + 0.005 < head_val_top1:
            log("Дообучение хуже головы более чем на 0.5 п.п. — возвращаю голову.")
            # skip_mismatch: после compile у Adam больше слотов, чем в файле головы.
            model.load_weights(pre_path, skip_mismatch=True)
            kept = "head"
            val_top1, val_top5 = head_val_top1, val_top5
        else:
            val_top1, val_top5 = ft_top1, ft_top5
        finetune_info = {
            "epochs": args.finetune_epochs,
            "images": len(ft_paths),
            "unfrozen_layers": opened,
            "lr": args.finetune_lr,
            "seconds": round(ft_sec, 1),
            "val_top1_before": round(head_val_top1, 4),
            "val_top1_after": round(ft_top1, 4),
            "val_top5_after": round(ft_top5, 4),
            "kept": kept,
            "layer_probe": probe.name,
            "weight_l1_delta": round(abs(probe_after - probe_before), 6),
        }

    log("Финальная оценка на официальном test Food-101…")
    test_top1, test_top5, per_ok, per_n = evaluate(model, test_p, test_y, args.img_size, args.batch)
    log(f"TEST top1 {test_top1:.4f}  top5 {test_top5:.4f}  n={len(test_p)}")
    keras_path = "/tmp/caloric_scan.keras"
    model.save(keras_path)
    log("Сохранил " + keras_path)

    per_class = []
    for i, name in enumerate(classes):
        n_i = int(per_n[i])
        per_class.append(
            {
                "id": name,
                "n": n_i,
                "top1": round(float(per_ok[i] / n_i), 4) if n_i else None,
            }
        )

    conversion = None
    convert_error = None
    if not args.skip_convert:
        try:
            conversion = export_tfjs(model, args.out, args.img_size)
            log(f"TFJS {conversion['bytes'] / (1024 * 1024):.2f} МБ")
        except Exception as exc:  # noqa: BLE001 — метрики важнее падения конвертера
            convert_error = f"{type(exc).__name__}: {exc}"
            log("Конвертация не удалась: " + convert_error)

    metrics = {
        "date": dt.date.today().isoformat(),
        "tensorflow": tf.__version__,
        "backbone": "MobileNetV2",
        "pretrained": "imagenet",
        "img_size": args.img_size,
        "train_images": len(train_p),
        "val_images": len(val_p),
        "test_images": len(test_p),
        "test_split": "official Food-101 test",
        "limit_per_class": args.limit_per_class,
        "holdout_per_class": args.holdout_per_class,
        "test_per_class": args.test_per_class,
        "head_epochs_ran": len(head_hist.get("accuracy", [])),
        "head_val_accuracy_last": head_hist.get("val_accuracy", [None])[-1],
        "internal_val_top1": round(float(val_top1), 4),
        "internal_val_top5": round(float(val_top5), 4),
        "test_top1": round(float(test_top1), 4),
        "test_top5": round(float(test_top5), 4),
        "feature_extract_seconds": round(train_feat_sec, 1),
        "total_seconds": round(time.time() - t_all, 1),
        "finetune": finetune_info,
        "conversion": conversion,
        "convert_error": convert_error,
        "per_class_test_top1": per_class,
        "note": (
            "test_top1/test_top5 измерены на официальном test-сплите Food-101, "
            "который не использовался для обучения и выбора чекпоинта. "
            "internal_val — holdout из train, по нему решали, оставить ли дообучение."
        ),
    }
    os.makedirs(os.path.dirname(args.metrics), exist_ok=True)
    with open(args.metrics, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, ensure_ascii=False, indent=2)
    meta = {
        "version": "v1",
        "backbone": "MobileNetV2",
        "pretrained": "imagenet",
        "img_size": args.img_size,
        "quantize": "float16",
        "test_top1": metrics["test_top1"],
        "test_top5": metrics["test_top5"],
        "test_images": metrics["test_images"],
        "train_images": metrics["train_images"],
    }
    if not args.skip_convert:
        with open(os.path.join(args.out, "metadata.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
    log("Готово: " + args.metrics)


if __name__ == "__main__":
    main()
