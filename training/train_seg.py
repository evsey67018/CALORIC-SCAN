#!/usr/bin/env python3
"""Сегментация ингредиентов FoodSeg103 поверх уже обученного Food-101 MobileNetV2.

Веса backbone и головы блюда берутся из чекпоинта v1 (не с ImageNet заново).
Ранние блоки заморожены. Если дообучение верхних блоков роняет Food-101
ниже 61.1% top-1 на официальном тесте, эти блоки откатываются.
"""
from __future__ import annotations

import argparse
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
import train as dishtrain

IMG = 224
OUT = 56
NUM_SEG = 104
NUM_DISH = 101
# Опубликованный top-1 v1 равен 0.6106 (это и есть «61,1%»).
FOOD101_FLOOR = 0.6106


def log(msg: str) -> None:
    print(msg, flush=True)


def load_dish_and_backbone(weights_path: str):
    """Та же схема, что в v1, и загрузка обученных весов."""
    base160 = tf.keras.applications.MobileNetV2(
        input_shape=(160, 160, 3),
        include_top=False,
        weights=None,
    )
    inputs = tf.keras.Input(shape=(160, 160, 3), name="image")
    x = tf.keras.layers.Rescaling(1.0 / 127.5, offset=-1.0, name="preprocess")(inputs)
    x = base160(x, training=False)
    x = tf.keras.layers.GlobalAveragePooling2D(name="gap")(x)
    x = tf.keras.layers.Dropout(0.3, name="drop")(x)
    out = tf.keras.layers.Dense(
        NUM_DISH,
        activation="softmax",
        kernel_regularizer=tf.keras.regularizers.l2(1e-4),
        name="probs",
    )(x)
    dish = tf.keras.Model(inputs, out, name="caloric_scan_dish")
    dish.load_weights(weights_path, skip_mismatch=True)
    return dish, base160


def copy_backbone(src, dst) -> None:
    named = {layer.name: layer for layer in src.layers}
    copied = 0
    for layer in dst.layers:
        if not layer.weights or layer.name not in named:
            continue
        layer.set_weights(named[layer.name].get_weights())
        copied += 1
    if copied < 20:
        raise RuntimeError(f"Скопировано только {copied} слоёв backbone")
    log(f"Скопировано слоёв backbone: {copied}")


def snapshot_backbone(base) -> dict:
    return {
        layer.name: [w.numpy().copy() for w in layer.weights]
        for layer in base.layers
        if layer.weights
    }


def restore_backbone(base, snap: dict) -> None:
    for layer in base.layers:
        if layer.name in snap:
            layer.set_weights(snap[layer.name])


def pick_skip_layers(base) -> dict:
    chosen = {}
    for layer in base.layers:
        shape = getattr(layer, "output_shape", None)
        if not isinstance(shape, tuple) or len(shape) != 4:
            continue
        size = shape[1]
        if size in (56, 28, 14, 7):
            chosen[size] = layer.name
    log("Съёмы признаков: " + ", ".join(f"{k}:{v}" for k, v in sorted(chosen.items())))
    if 7 not in chosen or 14 not in chosen or 28 not in chosen:
        raise RuntimeError(f"Не нашёл карты признаков: {chosen}")
    return chosen


def conv_bn_relu(x, filters, name):
    x = tf.keras.layers.Conv2D(filters, 1, padding="same", use_bias=False, name=name + "_conv")(x)
    x = tf.keras.layers.BatchNormalization(name=name + "_bn")(x)
    return tf.keras.layers.ReLU(name=name + "_relu")(x)


def build_seg_model(base224, skips: dict):
    raw = tf.keras.Input(shape=(IMG, IMG, 3), name="image")
    x = tf.keras.layers.Rescaling(1.0 / 127.5, offset=-1.0, name="preprocess")(raw)
    names = [skips[56], skips[28], skips[14], skips[7]] if 56 in skips else [skips[28], skips[14], skips[7]]
    feat = tf.keras.Model(base224.input, [base224.get_layer(n).output for n in names], name="food_encoder")
    maps = feat(x, training=False)
    if not isinstance(maps, (list, tuple)):
        maps = [maps]
    # От глубокой карты к мелкой: 7 → 14 → 28 → 56.
    deep = maps[-1]
    y = conv_bn_relu(deep, 96, "dec7")
    for scale, tensor in zip((14, 28, 56), reversed(maps[:-1])):
        y = tf.keras.layers.UpSampling2D(name=f"up_{scale}")(y)
        side = conv_bn_relu(tensor, 96, f"dec{scale}")
        y = tf.keras.layers.Add(name=f"add_{scale}")([y, side])
        y = tf.keras.layers.ReLU(name=f"fuse_{scale}")(y)
    if int(y.shape[1]) != OUT:
        y = tf.keras.layers.UpSampling2D(size=(OUT // int(y.shape[1]), OUT // int(y.shape[2])), name="up_out")(y)
    logits = tf.keras.layers.Conv2D(NUM_SEG, 1, name="logits")(y)
    model = tf.keras.Model(raw, logits, name="caloric_seg")
    return model, feat


def freeze_backbone(base, unfreeze_top: bool) -> int:
    opened = 0
    for layer in base.layers:
        name = layer.name
        top = unfreeze_top and (
            name.startswith("block_15") or name.startswith("block_16") or name.startswith("Conv_1")
        )
        if isinstance(layer, tf.keras.layers.BatchNormalization) or not top:
            layer.trainable = False
        else:
            layer.trainable = True
            opened += 1
    return opened


def export_split(split: str, src_dir: str, dst_dir: str) -> int:
    """Сохраняет JPEG 224 и маску 56 (класс = значение пикселя)."""
    from datasets import load_dataset

    os.makedirs(dst_dir, exist_ok=True)
    marker = os.path.join(dst_dir, "done.txt")
    if os.path.exists(marker):
        n = int(open(marker).read().strip())
        log(f"{split}: уже экспортировано {n}")
        return n
    ds = load_dataset(src_dir, split=split)
    n = 0
    for row in ds:
        img = row["image"].convert("RGB").resize((IMG, IMG), Image.BILINEAR)
        mask = np.array(row["label"])
        if mask.ndim == 3:
            mask = mask[:, :, 0]
        mask = Image.fromarray(mask.astype(np.uint8), mode="L").resize((OUT, OUT), Image.NEAREST)
        img.save(os.path.join(dst_dir, f"{n:05d}.jpg"), quality=90)
        mask.save(os.path.join(dst_dir, f"{n:05d}.png"))
        n += 1
        if n % 500 == 0:
            log(f"  экспорт {split} {n}")
    with open(marker, "w") as fh:
        fh.write(str(n))
    log(f"{split}: экспортировано {n}")
    return n


def _read_pair(path):
    raw = tf.io.read_file(path)
    img = tf.io.decode_jpeg(raw, channels=3)
    img = tf.image.resize(img, [IMG, IMG])
    img.set_shape([IMG, IMG, 3])
    mask_path = tf.strings.regex_replace(path, r"\.jpg$", ".png")
    mask = tf.io.decode_png(tf.io.read_file(mask_path), channels=1)
    mask = tf.image.resize(mask, [OUT, OUT], method="nearest")
    mask = tf.cast(mask[:, :, 0], tf.int32)
    return img, mask


def load_eval(path):
    return _read_pair(path)


def load_train(path):
    img, mask = _read_pair(path)
    flip = tf.random.uniform(()) < 0.5
    img = tf.cond(flip, lambda: tf.image.flip_left_right(img), lambda: img)
    mask3 = mask[..., None]
    mask3 = tf.cond(flip, lambda: tf.image.flip_left_right(mask3), lambda: mask3)
    mask = mask3[..., 0]
    img = tf.image.random_brightness(img, 20.0)
    img = tf.image.random_contrast(img, 0.9, 1.1)
    img = tf.clip_by_value(img, 0.0, 255.0)
    return img, mask


def file_ds(folder: str, training: bool, batch: int):
    paths = sorted(
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.endswith(".jpg")
    )
    ds = tf.data.Dataset.from_tensor_slices(paths)
    if training:
        ds = ds.shuffle(min(len(paths), 2048), reshuffle_each_iteration=True)
    ds = ds.map(load_train if training else load_eval, num_parallel_calls=4, deterministic=not training)
    return ds.batch(batch).prefetch(2), len(paths)


def seg_loss(y_true, y_pred):
    ce = tf.keras.losses.sparse_categorical_crossentropy(y_true, y_pred, from_logits=True)
    weight = tf.where(tf.equal(y_true, 0), 0.2, 1.0)
    weight = tf.cast(weight, ce.dtype)
    return tf.reduce_sum(ce * weight) / tf.reduce_sum(weight)


def confusion_update(conf, pred, gt):
    pred = pred.reshape(-1).astype(np.int64)
    gt = gt.reshape(-1).astype(np.int64)
    valid = (gt >= 0) & (gt < NUM_SEG) & (pred >= 0) & (pred < NUM_SEG)
    idx = gt[valid] * NUM_SEG + pred[valid]
    conf += np.bincount(idx, minlength=NUM_SEG * NUM_SEG).reshape(NUM_SEG, NUM_SEG)


def miou_from_conf(conf) -> dict:
    ious = []
    present = []
    for c in range(1, NUM_SEG):
        tp = conf[c, c]
        union = conf[c, :].sum() + conf[:, c].sum() - tp
        if union <= 0:
            continue
        iou = float(tp / union)
        ious.append(iou)
        present.append({"id": int(c), "iou": round(iou, 4)})
    pix = float(np.trace(conf) / max(conf.sum(), 1))
    return {
        "miou_food": float(np.mean(ious)) if ious else 0.0,
        "classes_present": len(present),
        "pixel_acc": pix,
        "per_class": present,
    }


def evaluate_seg(model, folder: str, batch: int) -> dict:
    ds, n = file_ds(folder, training=False, batch=batch)
    conf56 = np.zeros((NUM_SEG, NUM_SEG), dtype=np.int64)
    conf224 = np.zeros((NUM_SEG, NUM_SEG), dtype=np.int64)
    seen = 0
    for img, mask56 in ds:
        logits = model(img, training=False).numpy()
        pred56 = logits.argmax(axis=-1).astype(np.int64)
        gt56 = mask56.numpy().astype(np.int64)
        confusion_update(conf56, pred56, gt56)
        pred_img = tf.image.resize(pred56[..., None], [IMG, IMG], method="nearest").numpy().astype(np.int64)[..., 0]
        # Маска на диске уже 56. Для 224 сравниваем с ближайшим увеличением GT.
        gt_img = tf.image.resize(gt56[..., None], [IMG, IMG], method="nearest").numpy().astype(np.int64)[..., 0]
        confusion_update(conf224, pred_img, gt_img)
        seen += int(img.shape[0])
        if seen % 512 < batch:
            log(f"  seg-eval {seen}/{n}")
    out56 = miou_from_conf(conf56)
    out224 = miou_from_conf(conf224)
    return {"images": seen, "at_56": out56, "at_224": out224}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--weights", default="/tmp/caloric_head.weights.h5")
    parser.add_argument("--food101", default="/tmp/food-extract/food-101")
    parser.add_argument("--foodseg", default="/tmp/foodseg103")
    parser.add_argument("--cache", default="/tmp/foodseg_cache")
    parser.add_argument("--out", default=os.path.join(ROOT, "model", "seg"))
    parser.add_argument("--epochs", type=int, default=6)
    parser.add_argument("--finetune-epochs", type=int, default=1)
    parser.add_argument("--batch", type=int, default=12)
    parser.add_argument("--decoder-lr", type=float, default=1e-3)
    parser.add_argument("--finetune-lr", type=float, default=1e-5)
    args = parser.parse_args()

    tf.keras.utils.set_random_seed(42)
    tf.config.threading.set_intra_op_parallelism_threads(4)
    tf.config.threading.set_inter_op_parallelism_threads(1)

    log("Загружаю чекпоинт Food-101…")
    dish, base160 = load_dish_and_backbone(args.weights)
    dish_head = [w.copy() for w in dish.get_layer("probs").get_weights()]

    base224 = tf.keras.applications.MobileNetV2(
        input_shape=(IMG, IMG, 3),
        include_top=False,
        weights=None,
    )
    copy_backbone(base160, base224)
    original = snapshot_backbone(base224)
    skips = pick_skip_layers(base224)
    seg, _feat = build_seg_model(base224, skips)
    log(f"Выход сегментации: {seg.output_shape}")

    train_dir = os.path.join(args.cache, "train")
    val_dir = os.path.join(args.cache, "validation")
    export_split("train", args.foodseg, train_dir)
    export_split("validation", args.foodseg, val_dir)
    train_ds, n_train = file_ds(train_dir, True, args.batch)
    val_ds, n_val = file_ds(val_dir, False, args.batch)
    log(f"train {n_train}  val {n_val}")

    freeze_backbone(base224, unfreeze_top=False)
    seg.compile(optimizer=tf.keras.optimizers.Adam(args.decoder_lr), loss=seg_loss)
    log("Учу декодер, backbone заморожен (веса Food-101 не двигаются)…")
    seg.fit(train_ds, validation_data=val_ds, epochs=args.epochs, verbose=2)
    decoder_weights = {
        layer.name: [w.numpy().copy() for w in layer.weights]
        for layer in seg.layers
        if layer.weights and layer.name.startswith(("dec", "logits"))
    }

    finetune_info = {"epochs": 0, "kept_backbone": "frozen_food101"}
    if args.finetune_epochs > 0:
        opened = freeze_backbone(base224, unfreeze_top=True)
        seg.compile(optimizer=tf.keras.optimizers.Adam(args.finetune_lr), loss=seg_loss)
        log(f"Короткое дообучение верхних блоков, lr {args.finetune_lr}, слоёв {opened}")
        seg.fit(train_ds, validation_data=val_ds, epochs=args.finetune_epochs, verbose=2)
        # Проверка Food-101 на подвыборке до полного теста.
        copy_backbone(base224, base160)
        classes = dishtrain.load_classes(args.food101)
        groups = dishtrain.read_groups(args.food101, "test", classes)
        probe_p, probe_y = dishtrain.take_test(groups, classes, np.random.RandomState(0), 8)
        # База для сравнения: оригинальные веса на тех же фото.
        restore_backbone(base160, {k: original[k] for k in original})
        # base160 snapshot was from food101, original is copy of that. probe baseline:
        base_top1, _, _, _ = dishtrain.evaluate(dish, probe_p, probe_y, 160, 64)
        copy_backbone(base224, base160)
        tuned_top1, _, _, _ = dishtrain.evaluate(dish, probe_p, probe_y, 160, 64)
        log(f"Food-101 probe {len(probe_p)}: было {base_top1:.4f}  стало {tuned_top1:.4f}")
        if tuned_top1 + 0.005 < base_top1:
            log("Подвыборка просела — возвращаю backbone и декодер к замороженному этапу.")
            restore_backbone(base224, original)
            copy_backbone(base224, base160)
            for layer in seg.layers:
                if layer.name in decoder_weights:
                    layer.set_weights(decoder_weights[layer.name])
            kept = "frozen_food101"
        else:
            kept = "top_blocks_finetuned"
        finetune_info = {
            "epochs": args.finetune_epochs,
            "lr": args.finetune_lr,
            "unfrozen_layers": opened,
            "probe_images": len(probe_p),
            "probe_top1_before": round(float(base_top1), 4),
            "probe_top1_after": round(float(tuned_top1), 4),
            "kept_backbone": kept,
        }

    log("FoodSeg103 validation mIoU…")
    seg_metrics = evaluate_seg(seg, val_dir, args.batch)
    log(json.dumps(seg_metrics, ensure_ascii=False))

    log("Официальный тест Food-101…")
    classes = dishtrain.load_classes(args.food101)
    dishtrain.assert_labels(os.path.join(ROOT, "data", "labels.json"), classes)
    test_groups = dishtrain.read_groups(args.food101, "test", classes)
    test_p, test_y = dishtrain.take_test(test_groups, classes, np.random.RandomState(43), 0)
    copy_backbone(base224, base160)
    # Голова блюда должна остаться прежней.
    dish.get_layer("probs").set_weights(dish_head)
    top1, top5, _, _ = dishtrain.evaluate(dish, test_p, test_y, 160, 64)
    log(f"Food-101 TEST top1 {top1:.4f} top5 {top5:.4f}")
    if top1 < FOOD101_FLOOR - 0.0005:
        log("Ниже чекпоинта v1 (61,1%) — откат backbone.")
        restore_backbone(base224, original)
        copy_backbone(base224, base160)
        dish.get_layer("probs").set_weights(dish_head)
        for layer in seg.layers:
            if layer.name in decoder_weights:
                layer.set_weights(decoder_weights[layer.name])
        top1, top5, _, _ = dishtrain.evaluate(dish, test_p, test_y, 160, 64)
        seg_metrics = evaluate_seg(seg, val_dir, args.batch)
        finetune_info["reverted_full_test"] = True
        log(f"После отката top1 {top1:.4f} top5 {top5:.4f}")
    if top1 < FOOD101_FLOOR - 0.0005:
        raise SystemExit(f"Food-101 top-1 {top1:.4f} ниже чекпоинта v1 {FOOD101_FLOOR:.4f}")

    os.makedirs(args.out, exist_ok=True)
    saved = "/tmp/caloric_seg_saved"
    if os.path.isdir(saved):
        import shutil
        shutil.rmtree(saved)

    @tf.function(input_signature=[tf.TensorSpec([None, IMG, IMG, 3], tf.float32, name="image")])
    def serve(image):
        return seg(image, training=False)

    tf.saved_model.save(seg, saved, signatures={"serving_default": serve})
    if os.path.isdir(args.out):
        for name in os.listdir(args.out):
            if name == "model.json" or name.endswith(".bin"):
                os.remove(os.path.join(args.out, name))
    cmd = [
        sys.executable,
        os.path.join(ROOT, "training", "tfjs_convert.py"),
        saved,
        args.out,
        "--input_format", "tf_saved_model",
        "--output_format", "tfjs_graph_model",
        "--signature_name", "serving_default",
        "--saved_model_tags", "serve",
        "--quantize_float16",
        "--weight_shard_size_bytes", "4194304",
    ]
    log("Конвертация: " + " ".join(cmd))
    import subprocess
    subprocess.check_call(cmd)
    total = 0
    files = []
    for name in sorted(os.listdir(args.out)):
        path = os.path.join(args.out, name)
        if os.path.isfile(path) and (name == "model.json" or name.endswith(".bin")):
            size = os.path.getsize(path)
            total += size
            files.append({"file": name, "bytes": size})
    metrics = {
        "dataset": "FoodSeg103",
        "split": "validation",
        "val_images": seg_metrics["images"],
        "img_size": IMG,
        "output_size": OUT,
        "miou_food_at_56": round(seg_metrics["at_56"]["miou_food"], 4),
        "miou_food_at_224": round(seg_metrics["at_224"]["miou_food"], 4),
        "pixel_acc_at_224": round(seg_metrics["at_224"]["pixel_acc"], 4),
        "classes_present": seg_metrics["at_224"]["classes_present"],
        "iou_low": sorted(seg_metrics["at_224"].get("per_class", []), key=lambda r: r["iou"])[:8],
        "iou_high": sorted(seg_metrics["at_224"].get("per_class", []), key=lambda r: -r["iou"])[:8],
        "food101_test_top1": round(float(top1), 4),
        "food101_test_top5": round(float(top5), 4),
        "food101_test_images": len(test_p),
        "food101_floor": FOOD101_FLOOR,
        "init": "Food-101 MobileNetV2 checkpoint v1 (not ImageNet from scratch)",
        "finetune": finetune_info,
        "conversion": {"files": files, "bytes": total, "quantize": "float16"},
        "note": (
            "mIoU считается по 103 пищевым классам (фон не входит в среднее), "
            "только по классам, которые встретились в validation. "
            "at_224 — маска, растянутая ближайшим соседом до входа модели."
        ),
    }
    with open(os.path.join(ROOT, "training", "seg_metrics.json"), "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, ensure_ascii=False, indent=2)
    meta = {
        "version": "seg-v2",
        "task": "semantic-segmentation",
        "dataset": "FoodSeg103",
        "img_size": IMG,
        "output_size": OUT,
        "classes": NUM_SEG,
        "miou_food": metrics["miou_food_at_224"],
        "pixel_acc": metrics["pixel_acc_at_224"],
        "food101_test_top1": metrics["food101_test_top1"],
        "food101_test_top5": metrics["food101_test_top5"],
        "quantize": "float16",
        "bytes": total,
    }
    with open(os.path.join(args.out, "metadata.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    log(f"TFJS seg {total/1024/1024:.2f} МБ")
    log("Готово")


if __name__ == "__main__":
    main()
