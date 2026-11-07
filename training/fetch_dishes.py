#!/usr/bin/env python3
"""Параллельная закачка до ~100 фото на блюдо из каталога (цель ~1M).

Источники: локальный Food-101 (train, без официального test), уже скачанные
превью, Wikimedia Commons, Openverse, DuckDuckGo. Картинки лежат в
/tmp/mem_store, пока embed_incoming.py не превратит их в int8-чанки и не сотрёт.
Скрипт сам тормозит, если входящих файлов слишком много.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import time
import urllib.parse

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = "/tmp/memory_images/catalog.json"
STORE = "/tmp/mem_store"
DB = "/tmp/mem_ledger.sqlite"
UA = "CaloricScanBot/1.0 (food embeddings; local research)"
TARGET = 100
MAX_INCOMING = 25000
FOOD101 = "/tmp/food-extract/food-101"


def log(msg: str) -> None:
    print(msg, flush=True)


def connect() -> sqlite3.Connection:
    os.makedirs(STORE, exist_ok=True)
    db = sqlite3.connect(DB, timeout=60)
    db.execute("CREATE TABLE IF NOT EXISTS seen (md5 TEXT PRIMARY KEY, class_id TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS counts (class_id TEXT PRIMARY KEY, n INTEGER)")
    return db


def count_of(db: sqlite3.Connection, class_id: str) -> int:
    row = db.execute("SELECT n FROM counts WHERE class_id=?", (class_id,)).fetchone()
    return int(row[0]) if row else 0


def add_file(db: sqlite3.Connection, class_id: str, digest: str, src: str, dest: str) -> bool:
    try:
        db.execute("INSERT INTO seen(md5, class_id) VALUES (?,?)", (digest, class_id))
    except sqlite3.IntegrityError:
        if os.path.exists(dest) and dest != src:
            try:
                os.remove(dest)
            except OSError:
                pass
        return False
    db.execute(
        "INSERT INTO counts(class_id, n) VALUES (?,1) ON CONFLICT(class_id) DO UPDATE SET n=n+1",
        (class_id,),
    )
    db.commit()
    return True


def link_or_copy(src: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        return
    try:
        os.link(src, dest)
    except OSError:
        import shutil
        shutil.copy2(src, dest)


def seed_existing(db: sqlite3.Connection) -> int:
    n = 0
    root = "/tmp/memory_images"
    if not os.path.isdir(root):
        return 0
    for class_id in os.listdir(root):
        folder = os.path.join(root, class_id)
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if not name.endswith(".jpg"):
                continue
            src = os.path.join(folder, name)
            digest = hashlib.md5(open(src, "rb").read()).hexdigest()
            dest = os.path.join(STORE, class_id, digest[:16] + ".jpg")
            if count_of(db, class_id) >= TARGET:
                break
            link_or_copy(src, dest)
            if add_file(db, class_id, digest, "previous", dest):
                n += 1
    log(f"уже было фото: {n}")
    return n


def seed_food101(db: sqlite3.Connection, catalog_ids: set[str]) -> int:
    meta = os.path.join(FOOD101, "meta", "train.txt")
    if not os.path.exists(meta):
        return 0
    labels = json.load(open(os.path.join(ROOT, "data", "labels.json"), encoding="utf-8"))
    classes = [row["id"] for row in labels]
    groups = {c: [] for c in classes}
    with open(meta, encoding="utf-8") as fh:
        for line in fh:
            rel = line.strip()
            if not rel or "/" not in rel:
                continue
            cls, _name = rel.split("/", 1)
            if cls in groups:
                groups[cls].append(os.path.join(FOOD101, "images", rel + ".jpg"))
    rng = np.random.RandomState(42)
    added = 0
    for cls in classes:
        if cls not in catalog_ids:
            continue
        items = list(groups[cls])
        rng.shuffle(items)
        # Первые 20 — внутренняя проверка train.py, в банк не кладём.
        for src in items[20:20 + TARGET]:
            if count_of(db, cls) >= TARGET or not os.path.exists(src):
                continue
            digest = hashlib.md5(open(src, "rb").read()).hexdigest()
            dest = os.path.join(STORE, cls, digest[:16] + ".jpg")
            link_or_copy(src, dest)
            if add_file(db, cls, digest, "food101", dest):
                added += 1
    log(f"Food-101 в очередь: {added}")
    return added


def ingest_tree(db: sqlite3.Connection, root: str, class_from_path) -> int:
    """Подхватывает уже распакованный Food2K/ISIA, если каталог появился."""
    if not os.path.isdir(root):
        return 0
    added = 0
    for dirpath, _dirs, files in os.walk(root):
        class_id = class_from_path(dirpath)
        if not class_id or count_of(db, class_id) >= TARGET:
            continue
        for name in files:
            if not name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                continue
            if count_of(db, class_id) >= TARGET:
                break
            src = os.path.join(dirpath, name)
            try:
                digest = hashlib.md5(open(src, "rb").read()).hexdigest()
            except OSError:
                continue
            dest = os.path.join(STORE, class_id, digest[:16] + ".jpg")
            link_or_copy(src, dest)
            if add_file(db, class_id, digest, "dataset", dest):
                added += 1
    if added:
        log(f"локальный датасет {root}: {added}")
    return added


async def fetch_bytes(session, url: str) -> bytes | None:
    import aiohttp
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            data = await resp.read()
    except Exception:
        return None
    if len(data) < 4000 or len(data) > 6_000_000:
        return None
    if data[:3] != b"\xff\xd8\xff" and data[:8] != b"\x89PNG\r\n\x1a\n" and data[:4] != b"RIFF":
        return None
    return data


async def commons_urls(session, query: str, limit: int) -> list[str]:
    urls = []
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": "50",
        "prop": "imageinfo",
        "iiprop": "url|mime",
        "iiurlwidth": "360",
        "maxlag": "5",
    }
    for _ in range(3):
        if len(urls) >= limit:
            break
        try:
            async with session.get(
                "https://commons.wikimedia.org/w/api.php",
                params=params,
                timeout=aiohttp_timeout(),
            ) as resp:
                if resp.status == 429:
                    await asyncio.sleep(3)
                    continue
                data = await resp.json(content_type=None)
        except Exception:
            break
        for page in (data.get("query") or {}).get("pages", {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            mime = info.get("mime") or ""
            if mime and not mime.startswith("image/"):
                continue
            url = info.get("thumburl") or info.get("url")
            if url:
                urls.append(url)
        cont = data.get("continue")
        if not cont:
            break
        params.update(cont)
    return urls[:limit]


def aiohttp_timeout():
    import aiohttp
    return aiohttp.ClientTimeout(total=25)


async def openverse_urls(session, query: str, limit: int) -> list[str]:
    urls = []
    for page in (1, 2):
        try:
            async with session.get(
                "https://api.openverse.org/v1/images/",
                params={"q": query, "page_size": "20", "page": str(page)},
                timeout=aiohttp_timeout(),
            ) as resp:
                if resp.status != 200:
                    break
                data = await resp.json(content_type=None)
        except Exception:
            break
        for row in data.get("results") or []:
            url = row.get("thumbnail") or row.get("url")
            if url:
                urls.append(url)
        if len(urls) >= limit:
            break
    return urls[:limit]


def ddg_urls(query: str, limit: int) -> list[str]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    try:
        with DDGS() as ddgs:
            rows = list(ddgs.images(query, max_results=limit, safesearch="moderate"))
    except Exception:
        return []
    return [row.get("image") for row in rows if row.get("image")][:limit]


async def save_urls(session, sem, db, class_id: str, urls: list[str], need: int) -> int:
    saved = 0
    loop = asyncio.get_running_loop()

    async def one(url: str) -> bool:
        async with sem:
            raw = await fetch_bytes(session, url)
        if not raw or count_of(db, class_id) >= TARGET:
            return False
        digest = hashlib.md5(raw).hexdigest()
        dest = os.path.join(STORE, class_id, digest[:16] + ".jpg")
        if os.path.exists(dest):
            return False

        def write() -> None:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + ".part"
            with open(tmp, "wb") as fh:
                fh.write(raw)
            os.replace(tmp, dest)

        await loop.run_in_executor(None, write)
        return add_file(db, class_id, digest, "web", dest)

    tasks = [asyncio.create_task(one(url)) for url in urls[: need + 15]]
    for fut in asyncio.as_completed(tasks):
        try:
            if await fut:
                saved += 1
        except Exception:
            pass
        if saved >= need:
            break
    return saved


def incoming_files() -> int:
    total = 0
    for _root, _dirs, files in os.walk(STORE):
        total += sum(1 for name in files if name.endswith(".jpg"))
    return total


async def fill_one(session, sem, db, dish: dict) -> int:
    class_id = dish["id"]
    have = count_of(db, class_id)
    need = TARGET - have
    if need <= 0:
        return 0
    query = dish.get("query") or dish.get("en") or class_id
    urls = []
    urls += await commons_urls(session, query, need + 10)
    if len(urls) < need:
        urls += await openverse_urls(session, query, need)
    if len(urls) < need // 2:
        loop = asyncio.get_running_loop()
        extra = await loop.run_in_executor(None, ddg_urls, query, min(40, need))
        urls += extra
    # Уникальные URL.
    seen = set()
    uniq = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            uniq.append(url)
    return await save_urls(session, sem, db, class_id, uniq, need)


async def main_async() -> None:
    import aiohttp

    catalog = json.load(open(CATALOG, encoding="utf-8"))
    db = connect()
    ids = {row["id"] for row in catalog}
    seed_existing(db)
    seed_food101(db, ids)
    ingest_tree(db, "/tmp/food2k_images", lambda p: os.path.basename(p).lower().replace(" ", "_")[:72])
    ingest_tree(db, "/tmp/isia_images", lambda p: os.path.basename(p).lower().replace(" ", "_")[:72])
    t0 = time.time()
    done_at_start = sum(count_of(db, row["id"]) for row in catalog)
    sem = asyncio.Semaphore(96)
    connector = aiohttp.TCPConnector(limit=128, ttl_dns_cache=300)
    async with aiohttp.ClientSession(headers={"User-Agent": UA}, connector=connector) as session:
        passes = 0
        while True:
            pending = [row for row in catalog if count_of(db, row["id"]) < TARGET]
            if not pending:
                log("все классы достигли цели")
                break
            passes += 1
            if passes > 4:
                log(f"останов после 4 проходов, ещё без цели: {len(pending)}")
                break
            log(f"проход {passes}: классов ниже {TARGET}: {len(pending)}")
            wave = 24
            for start in range(0, len(pending), wave):
                while incoming_files() > MAX_INCOMING:
                    log("ждём эмбеддер, входящих слишком много")
                    await asyncio.sleep(8)
                batch = pending[start : start + wave]
                results = await asyncio.gather(
                    *[fill_one(session, sem, db, row) for row in batch],
                    return_exceptions=True,
                )
                got = sum(r for r in results if isinstance(r, int))
                elapsed = max(time.time() - t0, 1)
                total = sum(count_of(db, row["id"]) for row in catalog)
                full = sum(1 for row in catalog if count_of(db, row["id"]) >= TARGET)
                progress = {
                    "images": total,
                    "dishes_at_target": full,
                    "catalog": len(catalog),
                    "target_per_dish": TARGET,
                    "img_per_min": round((total - done_at_start) / elapsed * 60, 1),
                    "incoming_files": incoming_files(),
                    "pass": passes,
                    "waves_done": start // wave + 1,
                }
                with open("/tmp/fetch_progress.json", "w", encoding="utf-8") as fh:
                    json.dump(progress, fh, ensure_ascii=False, indent=2)
                log(
                    f"проход {passes} волна {start//wave+1} +{got}  всего {total}  "
                    f"блюд по {TARGET}: {full}/{len(catalog)}  {progress['img_per_min']} img/min"
                )
    log("закачка закончила проходы по каталогу")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
