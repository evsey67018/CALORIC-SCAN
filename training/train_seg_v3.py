#!/usr/bin/env python3
"""Более сильный декодер FoodSeg103 поверх замороженного MobileNetV2 v1.

Энкодер копируется из /tmp/caloric_head.weights.h5 (тот же backbone, что на сайте).
Обучается только DeepLab-подобный декодер: ASPP по карте 28×28 и склейка с картой 56×56.
Выход 112×112. Старые файлы model/seg не перезаписываются — экспорт в /tmp/seg_v4_tfjs.
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import numpy as np
import tensorflow as tf
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import train_seg as segv2

IMG = 224
OUT = 112
NUM_SEG = 104


def log(msg: str) -> None:
    print(msg, flush=True)


def export_pairs(split: str, src_dir: str, dst_dir: str) -> int:
    from datasets import load_dataset

    os.makedirs(dst_dir, exist_ok=True)
    marker = os.path.join(dst_dir, "done.txt")
    if os.path.exists(marker):
        n = int(open(marker, encoding="utf-8").read().strip())
        log(f"{split}: уже {n}")
        return n
    ds = load_dataset(src_dir, split=split)
    n = 0
    for row in ds:
        img = row["image"].convert("RGB").resize((IMG, IMG), Image.BILINEAR)
        mask = np.array(row["label"])
        if mask.ndim == 3:
            mask = mask[:, :, 0]
        mask_img = Image.fromarray(mask.astype(np.uint8), mode="L").resize((OUT, OUT), Image.NEAREST)
        img.save(os.path.join(dst_dir, f"{n:05d}.jpg"), quality=90)
        mask_img.save(os.path.join(dst_dir, f"{n:05d}.png"))
        n += 1
        if n % 800 == 0:
            log(f"  экспорт {split} {n}")
    with open(marker, "w", encoding="utf-8") as fh:
        fh.write(str(n))
    log(f"{split}: {n}")
    return n


def class_weights(folder: str) -> np.ndarray:
    counts = np.ones(NUM_SEG, dtype=np.float64)
    names = sorted(n for n in os.listdir(folder) if n.endswith(".png"))
    step = max(1, len(names) // 600)
    for name in names[::step]:
        mask = np.array(Image.open(os.path.join(folder, name)))
        counts += np.bincount(mask.reshape(-1), minlength=NUM_SEG)
    freq = counts / counts.sum()
    w = 1.0 / np.log(1.02 + freq)
    w[0] = w[0] * 0.25
    w = np.clip(w, 0.2, 8.0)
    w = w / w.mean()
    return w.astype(np.float32)


def make_loss(weights: np.ndarray):
    table = tf.constant(weights)

    def loss(y_true, y_pred):
        y_true = tf.cast(y_true, tf.int32)
        ce = tf.nn.sparse_softmax_cross_entropy_with_logits(labels=y_true, logits=y_pred)
        pt = tf.exp(-tf.minimum(ce, 20.0))
        focal = ce * tf.pow(1.0 - pt, 0.5)
        w = tf.gather(table, tf.clip_by_value(y_true, 0, NUM_SEG - 1))
        return tf.reduce_sum(focal * w) / (tf.reduce_sum(w) + 1e-6)

    return loss


def bn_relu(x, name):
    x = tf.keras.layers.BatchNormalization(name=name + "_bn")(x)
    return tf.keras.layers.ReLU(name=name + "_relu")(x)


def sep(x, filters, name):
    x = tf.keras.layers.DepthwiseConv2D(3, padding="same", use_bias=False, name=name + "_dw")(x)
    x = bn_relu(x, name + "_d")
    x = tf.keras.layers.Conv2D(filters, 1, padding="same", use_bias=False, name=name + "_pw")(x)
    return bn_relu(x, name + "_p")


def aspp_block(x, filters):
    h = int(x.shape[1])
    branches = []
    b = tf.keras.layers.Conv2D(filters, 1, padding="same", use_bias=False, name="aspp_1")(x)
    branches.append(bn_relu(b, "aspp_1"))
    for i, rate in enumerate((2, 4, 6)):
        b = tf.keras.layers.DepthwiseConv2D(
            3, padding="same", dilation_rate=rate, use_bias=False, name=f"aspp_d{rate}"
        )(x)
        b = bn_relu(b, f"aspp_d{rate}")
        b = tf.keras.layers.Conv2D(filters, 1, padding="same", use_bias=False, name=f"aspp_p{rate}")(b)
        branches.append(bn_relu(b, f"aspp_p{rate}"))
    pooled = tf.keras.layers.GlobalAveragePooling2D(name="aspp_gap")(x)
    pooled = tf.keras.layers.Reshape((1, 1, pooled.shape[-1]))(pooled)
    pooled = tf.keras.layers.Conv2D(filters, 1, padding="same", use_bias=False, name="aspp_pool")(pooled)
    pooled = bn_relu(pooled, "aspp_pool")
    pooled = tf.keras.layers.UpSampling2D(size=(h, h), interpolation="bilinear", name="aspp_up")(pooled)
    y = tf.keras.layers.Concatenate(name="aspp_cat")(branches + [pooled])
    y = tf.keras.layers.Conv2D(filters, 1, padding="same", use_bias=False, name="aspp_out")(y)
    return bn_relu(y, "aspp_out")


def build(base, skips):
    raw = tf.keras.Input(shape=(IMG, IMG, 3), name="image")
    x = tf.keras.layers.Rescaling(1.0 / 127.5, offset=-1.0, name="preprocess")(raw)
    feat = tf.keras.Model(
        base.input,
        [base.get_layer(skips[28]).output, base.get_layer(skips[56]).output],
        name="food_encoder",
    )
    feat.trainable = False
    base.trainable = False
    deep, low = feat(x, training=False)
    y = aspp_block(deep, 128)
    y = tf.keras.layers.UpSampling2D(size=(2, 2), interpolation="bilinear", name="up56")(y)
    side = tf.keras.layers.Conv2D(48, 1, padding="same", use_bias=False, name="low_proj")(low)
    side = bn_relu(side, "low_proj")
    y = tf.keras.layers.Concatenate(name="fuse")([y, side])
    y = sep(y, 128, "dec1")
    y = sep(y, 96, "dec2")
    y = tf.keras.layers.UpSampling2D(size=(2, 2), interpolation="bilinear", name="up112")(y)
    logits = tf.keras.layers.Conv2D(NUM_SEG, 1, name="logits")(y)
    return tf.keras.Model(raw, logits, name="caloric_seg_v4")


def read_pair(path):
    raw = tf.io.read_file(path)
    img = tf.io.decode_jpeg(raw, channels=3)
    img = tf.image.resize(img, [IMG, IMG])
    img.set_shape([IMG, IMG, 3])
    mask_path = tf.strings.regex_replace(path, r"\.jpg$", ".png")
    mask = tf.io.decode_png(tf.io.read_file(mask_path), channels=1)
    mask = tf.image.resize(mask, [OUT, OUT], method="nearest")
    mask = tf.cast(mask[:, :, 0], tf.int32)
    return img, mask


def augment(img, mask):
    flip = tf.random.uniform(()) < 0.5
    img = tf.cond(flip, lambda: tf.image.flip_left_right(img), lambda: img)
    mask = tf.cond(flip, lambda: tf.image.flip_left_right(mask[..., None])[..., 0], lambda: mask)
    big = tf.image.resize(img, [248, 248])
    mbig = tf.image.resize(mask[..., None], [124, 124], method="nearest")
    oy = tf.random.uniform((), 0, 25, dtype=tf.int32)
    ox = tf.random.uniform((), 0, 25, dtype=tf.int32)
    img = tf.slice(big, [oy, ox, 0], [IMG, IMG, 3])
    my = oy // 2
    mx = ox // 2
    mask = tf.slice(mbig, [my, mx, 0], [OUT, OUT, 1])[..., 0]
    img.set_shape([IMG, IMG, 3])
    mask.set_shape([OUT, OUT])
    img = tf.image.random_brightness(img, 28.0)
    img = tf.image.random_contrast(img, 0.8, 1.25)
    img = tf.image.random_saturation(img, 0.8, 1.25)
    img = tf.image.random_hue(img, 0.04)
    img = tf.clip_by_value(img, 0.0, 255.0)
    return img, mask


def dataset(folder: str, training: bool, batch: int):
    paths = sorted(
        os.path.join(folder, name) for name in os.listdir(folder) if name.endswith(".jpg")
    )
    ds = tf.data.Dataset.from_tensor_slices(paths)
    if training:
        ds = ds.shuffle(min(len(paths), 2048), reshuffle_each_iteration=True)
    ds = ds.map(read_pair, num_parallel_calls=4, deterministic=not training)
    if training:
        ds = ds.map(augment, num_parallel_calls=4, deterministic=False)
    return ds.batch(batch).prefetch(2), len(paths)


def miou(model, folder: str, batch: int) -> dict:
    ds, n = dataset(folder, False, batch)
    conf = np.zeros((NUM_SEG, NUM_SEG), dtype=np.int64)
    # Тот же протокол, что у v2: маска, приведённая к 56×56.
    conf56 = np.zeros((NUM_SEG, NUM_SEG), dtype=np.int64)
    seen = 0
    for img, mask in ds:
        pred = model(img, training=False).numpy().argmax(axis=-1).astype(np.int64)
        gt = mask.numpy().astype(np.int64)
        segv2.confusion_update(conf, pred, gt)
        pred56 = tf.image.resize(pred[..., None], [56, 56], method="nearest").numpy().astype(np.int64)[..., 0]
        gt56 = tf.image.resize(gt[..., None], [56, 56], method="nearest").numpy().astype(np.int64)[..., 0]
        segv2.confusion_update(conf56, pred56, gt56)
        seen += int(img.shape[0])
        if seen % 640 < batch:
            log(f"  seg-eval {seen}/{n}")
    full = segv2.miou_from_conf(conf)
    at56 = segv2.miou_from_conf(conf56)
    return {"images": seen, "at_112": full, "at_56": at56}


def main():
    tf.keras.utils.set_random_seed(42)
    tf.config.threading.set_intra_op_parallelism_threads(4)
    tf.config.threading.set_inter_op_parallelism_threads(1)
    weights = "/tmp/caloric_head.weights.h5"
    cache = "/tmp/foodseg112"
    log("Чекпоинт v1…")
    _dish, base160 = segv2.load_dish_and_backbone(weights)
    base = tf.keras.applications.MobileNetV2(input_shape=(IMG, IMG, 3), include_top=False, weights=None)
    segv2.copy_backbone(base160, base)
    del _dish, base160
    skips = segv2.pick_skip_layers(base)
    model = build(base, skips)
    log(f"выход {model.output_shape}  параметров {model.count_params()}")

    train_dir = os.path.join(cache, "train")
    val_dir = os.path.join(cache, "validation")
    export_pairs("train", "/tmp/foodseg103", train_dir)
    export_pairs("validation", "/tmp/foodseg103", val_dir)
    weights_np = class_weights(train_dir)
    train_ds, n_train = dataset(train_dir, True, 8)
    val_ds, n_val = dataset(val_dir, False, 8)
    log(f"train {n_train} val {n_val}")
    model.compile(optimizer=tf.keras.optimizers.Adam(8e-4), loss=make_loss(weights_np))
    ckpt = "/tmp/seg_v4.weights.h5"
    model.fit(
        train_ds,
        validation_data=val_ds,
        epochs=8,
        verbose=2,
        callbacks=[
            tf.keras.callbacks.EarlyStopping(
                monitor="val_loss", mode="min", patience=2, restore_best_weights=True
            ),
            tf.keras.callbacks.ReduceLROnPlateau(
                monitor="val_loss", factor=0.5, patience=1, min_lr=1e-5, verbose=1
            ),
        ],
    )
    model.save_weights(ckpt)
    log("mIoU…")
    metrics = miou(model, val_dir, 8)
    out = {
        "miou_food_at_112": round(metrics["at_112"]["miou_food"], 4),
        "pixel_acc_at_112": round(metrics["at_112"]["pixel_acc"], 4),
        "miou_food_at_56": round(metrics["at_56"]["miou_food"], 4),
        "pixel_acc_at_56": round(metrics["at_56"]["pixel_acc"], 4),
        "images": metrics["images"],
        "before_miou_at_56": 0.1318,
        "iou_high": sorted(metrics["at_112"]["per_class"], key=lambda r: -r["iou"])[:8],
        "iou_low": sorted(metrics["at_112"]["per_class"], key=lambda r: r["iou"])[:8],
    }
    with open("/tmp/seg_v4_metrics.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    log(json.dumps(out, ensure_ascii=False))

    saved = "/tmp/caloric_seg_v4_saved"
    if os.path.isdir(saved):
        import shutil
        shutil.rmtree(saved)

    @tf.function(input_signature=[tf.TensorSpec([None, IMG, IMG, 3], tf.float32, name="image")])
    def serve(image):
        return model(image, training=False)

    tf.saved_model.save(model, saved, signatures={"serving_default": serve})
    export_dir = "/tmp/seg_v4_tfjs"
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
    log("Конвертация сегментации…")
    import subprocess
    subprocess.check_call(cmd)
    total = sum(
        os.path.getsize(os.path.join(export_dir, n))
        for n in os.listdir(export_dir)
        if n == "model.json" or n.endswith(".bin")
    )
    log(f"TFJS seg {total / 1024 / 1024:.2f} МБ  mIoU56 {out['miou_food_at_56']}")


if __name__ == "__main__":
    main()
