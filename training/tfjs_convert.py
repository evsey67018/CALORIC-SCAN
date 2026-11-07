#!/usr/bin/env python3
"""Обёртка tensorflowjs 3.18 под TensorFlow 2.21 и NumPy 2.

Старый tensorflow_hub не импортируется на TF 2.21 (нет tf.estimator и
tensorflow.python.training.tracking). Для SavedModel он не нужен, поэтому
подставляется пустышка до импорта конвертера.
"""
import os
import sys
import types

os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

import numpy as np

if not hasattr(np, "object"):
    np.object = object  # noqa: NPY002
if not hasattr(np, "bool"):
    np.bool = np.bool_

hub = types.ModuleType("tensorflow_hub")


def _hub_unused(*_args, **_kwargs):
    raise RuntimeError("Сконвертировать можно SavedModel, не модуль TF-Hub")


hub.Module = _hub_unused
hub.resolve = _hub_unused
sys.modules["tensorflow_hub"] = hub

from tensorflowjs.converters.converter import pip_main

if __name__ == "__main__":
    pip_main()
