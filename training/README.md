# Как переобучить модель

Нужны Python 3.12, CPU (GPU не обязателен) и около 15 ГБ свободного места под датасет. Сам датасет в git не коммитится.

## 1. Датасет Food-101

Официальный архив ETH Zürich (~4,7 ГБ):

```bash
wget -O /tmp/food-101.tar.gz https://data.vision.ee.ethz.ch/cvl/food-101.tar.gz
mkdir -p /tmp/food-extract
tar -xzf /tmp/food-101.tar.gz -C /tmp/food-extract
```

Внутри должны появиться `meta/classes.txt`, `meta/train.txt`, `meta/test.txt` и `images/<класс>/*.jpg` — 101 000 файлов.

## 2. Зависимости

```bash
pip install -r training/requirements.txt
```

Конвертация в браузерную модель идёт через `training/tfjs_convert.py`. Скрипт рассчитан на связку TensorFlow 2.21 + tensorflowjs 3.18: он подставляет заглушку вместо старого `tensorflow_hub` (на TF 2.21 тот не импортируется) и чинит алиасы NumPy 2.

## 3. Запуск

Из корня репозитория:

```bash
python training/train.py \
  --data /tmp/food-extract/food-101 \
  --out model \
  --img-size 160 \
  --batch 64 \
  --head-epochs 25 \
  --finetune-epochs 2 \
  --finetune-lr 1e-4
```

Что делает скрипт:

1. Берёт ImageNet-веса MobileNetV2 и замораживает свёрточную часть.
2. Считает признаки для обучающих фото (из официального `train`, 20 фото каждого класса отложены во внутреннюю проверку).
3. Обучает линейную голову на 101 класс.
4. Размораживает последние блоки (`block_15`, `block_16`, `Conv_1`, без BatchNorm) и дообучает их.
5. Оставляет тот вариант, у которого выше точность на внутренней проверке.
6. Один раз прогоняет официальный `test` (25 250 фото) — эти фото не участвовали в обучении.
7. Сохраняет графовую модель TFJS с весами float16 в `model/` и метрики в `training/metrics.json`.

Полезные флаги, если мало времени: `--limit-per-class 200` сокращает обучение, `--test-per-class 40` сокращает финальный замер, `--skip-convert` не собирает браузерную модель.

Порядок 101 класса обязан совпадать с `training/food101_classes.txt` (это копия `meta/classes.txt`: `cheesecake` стоит перед `cheese_plate`).

## 4. Таблица калорий

```bash
python training/build_nutrition.py
```

Скрипт ходит в USDA FoodData Central (SR Legacy и FNDDS, на 100 г) и пишет `data/nutrition.json` и `data/labels.json`. Ключ API — переменная `USDA_API_KEY`, иначе `DEMO_KEY` (у него жёсткий лимит). Если у блюда нет чистого совпадения, остаётся пометка `curated USDA reference`: ручная сводка по близким карточкам USDA.
