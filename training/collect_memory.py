#!/usr/bin/env python3
"""Каталог блюд и картинки для банка памяти.

Имена: Food-101, Food2K, списки Википедии (русские/СНГ и крупные кухни),
ручной список частых блюд включая поке. Картинки Википедии — по одной
миниатюре на статью. Для частых блюд дополнительно поиск картинок
(подпись = запрос). Сырые файлы в git не кладутся.

Остановка: не больше MAX_IMAGES скачиваний и MAX_MINUTES минут на поиск.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = "/tmp/memory_images"
UA = "CaloricScanBot/1.0 (educational embeddings; contact local research)"
MAX_IMAGES = 16000
MAX_DDG_MINUTES = 22
TARGET_COMMON = 48
SKIP_CAT = re.compile(
    r"restaurant|chef|people|companies|company|brand|winery|brewery|distill|"
    r"stub|template|wine|beer|cocktail|spirit|beverage|drink|chain|"
    r"by country|by city|cuisine of|list of",
    re.I,
)

# Частые блюда, которым нужен плотный набор, а не одна карточка энциклопедии.
# kcal/protein/fat/carbs на 100 г. source=curated — типичные значения, не лаборатория.
COMMON = [
    ("poke_bowl", "Поке", "poke bowl salmon", 136, 10.0, 4.5, 14.0),
    ("borscht", "Борщ", "борщ тарелка", 49, 1.8, 2.2, 5.4),
    ("green_borscht", "Зелёный борщ", "зелёный борщ", 48, 2.2, 2.0, 4.8),
    ("pelmeni", "Пельмени", "пельмени варёные", 248, 12.0, 12.0, 24.0),
    ("vareniki", "Вареники", "вареники тарелка", 200, 6.5, 5.0, 32.0),
    ("shawarma", "Шаурма", "шаурма", 215, 11.0, 10.0, 20.0),
    ("doner", "Донер", "донер кебаб", 220, 12.0, 11.0, 18.0),
    ("plov", "Плов", "плов с мясом", 178, 6.5, 7.0, 22.0),
    ("olivier", "Оливье", "салат оливье", 180, 4.5, 14.0, 8.0),
    ("vinegret", "Винегрет", "винегрет", 75, 1.4, 3.5, 9.0),
    ("herring_fur", "Селёдка под шубой", "селёдка под шубой", 175, 4.5, 13.0, 9.0),
    ("syrniki", "Сырники", "сырники со сметаной", 220, 12.0, 10.0, 20.0),
    ("blini", "Блины", "блины тарелка", 196, 6.0, 8.0, 25.0),
    ("oladyi", "Оладьи", "оладьи", 210, 6.0, 8.5, 28.0),
    ("golubtsy", "Голубцы", "голубцы", 130, 7.0, 7.0, 9.0),
    ("solyanka", "Солянка", "солянка суп", 55, 3.5, 3.0, 3.5),
    ("shchi", "Щи", "щи суп", 40, 1.8, 1.6, 4.5),
    ("okroshka", "Окрошка", "окрошка", 60, 2.5, 2.4, 6.0),
    ("rassolnik", "Рассольник", "рассольник", 42, 1.6, 1.8, 4.8),
    ("ukha", "Уха", "уха суп", 46, 5.0, 1.5, 2.5),
    ("kharcho", "Харчо", "харчо", 65, 4.0, 3.0, 5.0),
    ("beef_stroganoff", "Бефстроганов", "бефстроганов", 185, 14.0, 12.0, 5.0),
    ("kiev_cutlet", "Котлета по-киевски", "котлета по-киевски", 260, 16.0, 18.0, 8.0),
    ("cutlet", "Котлета", "котлета с гарниром", 220, 15.0, 15.0, 8.0),
    ("grechka", "Гречка", "гречневая каша", 110, 4.2, 2.5, 18.0),
    ("mashed_potato", "Картофельное пюре", "картофельное пюре", 90, 2.0, 3.5, 13.0),
    ("cheburek", "Чебурек", "чебурек", 280, 9.0, 16.0, 26.0),
    ("belyash", "Беляш", "беляш", 290, 9.0, 16.0, 28.0),
    ("pirozhki", "Пирожки", "пирожки с мясом", 270, 7.5, 12.0, 34.0),
    ("vatrushka", "Ватрушка", "ватрушка", 290, 8.0, 12.0, 38.0),
    ("khinkali", "Хинкали", "хинкали", 210, 11.0, 9.0, 22.0),
    ("khachapuri", "Хачапури", "хачапури по-аджарски", 270, 11.0, 15.0, 24.0),
    ("shashlik", "Шашлык", "шашлык", 200, 20.0, 12.0, 2.0),
    ("lula", "Люля-кебаб", "люля-кебаб", 220, 16.0, 16.0, 3.0),
    ("draniki", "Драники", "драники", 180, 3.5, 9.0, 21.0),
    ("manty", "Манты", "манты", 190, 9.0, 8.0, 20.0),
    ("lagman", "Лагман", "лагман", 120, 6.0, 4.5, 14.0),
    ("samsa", "Самса", "самса", 270, 9.0, 13.0, 30.0),
    ("chakhohbili", "Чахохбили", "чахохбили", 110, 10.0, 6.0, 4.0),
    ("dolma", "Долма", "долма", 150, 8.0, 8.0, 10.0),
    ("lobio", "\u041b\u043e\u0431\u0438\u043e", "\u043b\u043e\u0431\u0438\u043e", 120, 6.0, 5.0, 12.0),
    ("satsivi", "Сациви", "сациви", 180, 12.0, 13.0, 3.0),
    ("beshbarmak", "Бешбармак", "бешбармак", 190, 12.0, 10.0, 12.0),
    ("holodets", "Холодец", "холодец", 80, 8.0, 5.0, 1.0),
    ("forshmak", "Форшмак", "форшмак", 180, 8.0, 15.0, 3.0),
    ("mimosa", "Мимоза", "салат мимоза", 190, 6.0, 15.0, 7.0),
    ("crab_salad", "Крабовый салат", "крабовый салат", 160, 5.0, 12.0, 8.0),
    ("medovik", "Медовик", "торт медовик", 390, 5.5, 18.0, 52.0),
    ("napoleon", "Наполеон", "торт наполеон", 400, 5.0, 22.0, 45.0),
    ("seledka", "Сельдь", "сельдь солёная", 217, 19.0, 15.0, 0.0),
    ("tabaka", "Цыплёнок табака", "цыплёнок табака", 210, 18.0, 15.0, 1.0),
    ("acai_bowl", "Асаи-боул", "acai bowl", 140, 2.5, 5.0, 22.0),
    ("burrito", "Буррито", "burrito", 206, 8.0, 8.0, 26.0),
    ("kebab", "Кебаб", "kebab plate", 215, 16.0, 13.0, 8.0),
    ("butter_chicken", "Баттер чикен", "butter chicken", 180, 12.0, 11.0, 7.0),
    ("tikka_masala", "Тикка масала", "chicken tikka masala", 170, 12.0, 10.0, 7.0),
    ("tom_yum", "Том ям", "tom yum soup", 55, 4.0, 2.5, 4.5),
    ("onigiri", "Онигири", "onigiri", 170, 4.0, 2.0, 34.0),
    ("avocado_toast", "Тост с авокадо", "avocado toast", 190, 5.0, 11.0, 18.0),
    ("croissant", "Круассан", "croissant", 406, 8.0, 21.0, 46.0),
    ("fried_chicken", "Жареная курица", "fried chicken", 260, 19.0, 16.0, 9.0),
    ("quesadilla", "Кесадилья", "quesadilla", 280, 13.0, 14.0, 24.0),
    ("pad_see_ew", "Пад си ю", "pad see ew", 160, 6.0, 5.0, 22.0),
    ("tom_kha", "Том кха", "tom kha soup", 90, 4.0, 6.0, 5.0),
    ("pho_bo", "Фо бо", "pho bo", 70, 5.0, 2.0, 8.0),
    ("banh_mi", "Бань ми", "banh mi", 230, 10.0, 7.0, 32.0),
    ("ramen_tonkotsu", "Тонкоцу рамэн", "tonkotsu ramen", 90, 5.0, 3.5, 10.0),
    ("udon", "Удон", "udon noodle soup", 95, 4.0, 1.5, 16.0),
    ("tempura", "Тэмпура", "tempura", 230, 8.0, 12.0, 22.0),
    ("gyudon", "Гюдон", "gyudon", 160, 8.0, 6.0, 18.0),
    ("katsudon", "Кацудон", "katsudon", 180, 9.0, 7.0, 20.0),
    ("okonomiyaki", "Окономияки", "okonomiyaki", 170, 6.0, 8.0, 18.0),
    ("bibimbap_bowl", "Пибимпап", "bibimbap bowl", 130, 6.0, 4.0, 18.0),
    ("kimchi_fried_rice", "Кимчи поккымпап", "kimchi fried rice", 160, 5.0, 5.0, 24.0),
    ("bulgogi", "Пулькоги", "bulgogi", 180, 16.0, 10.0, 6.0),
    ("japchae", "Чапче", "japchae", 140, 4.0, 4.0, 22.0),
    ("tacos_al_pastor", "Тако аль пастор", "tacos al pastor", 200, 12.0, 9.0, 18.0),
    ("enchilada", "Энчилада", "enchiladas", 190, 9.0, 9.0, 18.0),
    ("guacamole_chips", "Гуакамоле", "guacamole", 160, 2.0, 14.0, 8.0),
    ("ceviche_bowl", "Севиче", "ceviche", 90, 12.0, 2.0, 6.0),
    ("paella_seafood", "Паэлья", "seafood paella", 160, 8.0, 5.0, 20.0),
    ("lasagna_plate", "Лазанья", "lasagna", 170, 9.0, 8.0, 15.0),
    ("carbonara_plate", "Карбонара", "spaghetti carbonara", 180, 8.0, 8.0, 20.0),
    ("risotto_plate", "Ризотто", "mushroom risotto", 160, 4.0, 6.0, 22.0),
    ("gnocchi_plate", "Ньокки", "gnocchi", 150, 4.0, 3.0, 28.0),
    ("margherita", "Пицца маргарита", "pizza margherita", 230, 10.0, 8.0, 30.0),
    ("fish_and_chips_plate", "Рыба с картофелем", "fish and chips", 230, 12.0, 12.0, 18.0),
    ("bangers_mash", "Сосиски с пюре", "bangers and mash", 180, 8.0, 10.0, 14.0),
    ("full_english", "Английский завтрак", "full english breakfast", 200, 11.0, 14.0, 8.0),
    ("oatmeal", "Овсянка", "oatmeal bowl", 80, 2.8, 1.8, 13.0),
    ("granola_bowl", "Гранола", "granola yogurt bowl", 180, 5.0, 6.0, 26.0),
    ("smoothie_bowl", "Смузи-боул", "smoothie bowl", 110, 2.0, 3.0, 20.0),
    ("falafel_plate", "Фалафель", "falafel plate", 180, 8.0, 9.0, 16.0),
    ("hummus_plate", "Хумус", "hummus plate", 180, 8.0, 10.0, 15.0),
    ("shakshuka", "Шакшука", "shakshuka", 110, 6.0, 7.0, 6.0),
    ("tabbouleh", "Табуле", "tabbouleh", 90, 2.5, 4.0, 12.0),
    ("moussaka", "Мусака", "moussaka", 140, 7.0, 8.0, 10.0),
    ("souvlaki", "Сувлаки", "souvlaki", 190, 16.0, 10.0, 8.0),
    ("gyro", "Гирос", "gyro wrap", 220, 12.0, 11.0, 18.0),
    ("pad_thai_plate", "Пад тай", "pad thai", 150, 6.0, 4.0, 22.0),
    ("green_curry", "Зелёное карри", "thai green curry", 120, 8.0, 7.0, 6.0),
    ("spring_roll_fresh", "Спринг-ролл", "fresh spring rolls", 110, 4.0, 2.0, 18.0),
    ("banh_xeo", "Бань сео", "banh xeo", 180, 6.0, 8.0, 22.0),
    ("congee", "Рисовая каша", "rice congee", 45, 1.5, 0.4, 9.0),
    ("dim_sum", "Димсам", "dim sum", 180, 8.0, 7.0, 20.0),
    ("peking_duck_plate", "Утка по-пекински", "peking duck", 240, 16.0, 18.0, 4.0),
    ("mapo_tofu", "Мапо тофу", "mapo tofu", 130, 8.0, 8.0, 6.0),
    ("kung_pao", "Курица кунг пао", "kung pao chicken", 170, 14.0, 9.0, 8.0),
    ("fried_rice_plate", "Жареный рис", "egg fried rice", 160, 4.5, 5.0, 24.0),
    ("chow_mein", "Чоу мейн", "chow mein", 150, 6.0, 5.0, 20.0),
    ("hotpot", "Хого", "chinese hot pot", 90, 7.0, 5.0, 4.0),
    ("biryani", "Бирьяни", "chicken biryani", 170, 8.0, 6.0, 20.0),
    ("samosa_plate", "Самоса", "samosa", 260, 5.0, 14.0, 28.0),
    ("dal", "Дал", "dal tadka", 110, 6.0, 3.5, 14.0),
    ("naan", "Наан", "naan bread", 290, 9.0, 7.0, 48.0),
    ("palak_paneer", "Палак панир", "palak paneer", 140, 8.0, 9.0, 6.0),
    ("masala_dosa", "Масала доса", "masala dosa", 160, 4.0, 6.0, 22.0),
    ("ramen_miso", "Мисо рамэн", "miso ramen", 80, 4.5, 2.5, 10.0),
    ("nigiri_set", "Нигири", "nigiri sushi set", 150, 8.0, 2.0, 24.0),
    ("sashimi_set", "Сашими", "sashimi set", 130, 18.0, 4.0, 4.0),
    ("poke_tuna", "Поке с тунцом", "tuna poke bowl", 130, 12.0, 3.5, 14.0),
    ("chicken_caesar", "Цезарь с курицей", "chicken caesar salad", 140, 10.0, 8.0, 6.0),
    ("greek_salad_plate", "Греческий салат", "greek salad", 110, 3.0, 8.0, 6.0),
    ("cobb_salad", "Кобб", "cobb salad", 150, 9.0, 11.0, 4.0),
    ("quinoa_bowl", "Боул с киноа", "quinoa bowl", 140, 6.0, 5.0, 18.0),
    ("steak_fries", "Стейк с картофелем", "steak and fries", 220, 18.0, 12.0, 10.0),
    ("meatballs", "Фрикадельки", "meatballs tomato sauce", 180, 12.0, 11.0, 8.0),
    ("chili_con_carne", "Чили кон карне", "chili con carne", 130, 8.0, 6.0, 10.0),
    ("clam_chowder_bowl", "Клэм-чаудер", "clam chowder", 90, 4.0, 4.0, 9.0),
    ("lobster_roll", "Лобстер-ролл", "lobster roll", 200, 12.0, 8.0, 20.0),
    ("pancakes_stack", "Панкейки", "pancake stack", 230, 6.0, 9.0, 32.0),
    ("waffles_plate", "Вафли", "waffles berries", 250, 6.0, 11.0, 32.0),
    ("french_toast_plate", "Французский тост", "french toast", 230, 8.0, 10.0, 28.0),
    ("eggs_benedict_plate", "Яйца бенедикт", "eggs benedict", 220, 11.0, 15.0, 12.0),
    ("omelette_plate", "Омлет", "omelette", 150, 10.0, 11.0, 2.0),
    ("tiramisu_slice", "Тирамису", "tiramisu", 280, 5.0, 15.0, 32.0),
    ("cheesecake_slice", "Чизкейк", "cheesecake slice", 320, 6.0, 22.0, 26.0),
    ("brownie", "Брауни", "chocolate brownie", 410, 5.0, 22.0, 50.0),
    ("cinnamon_roll", "Синнабон", "cinnamon roll", 370, 5.0, 14.0, 56.0),
    ("bagel_cream", "Бейгл", "bagel cream cheese", 280, 9.0, 8.0, 44.0),
    ("hot_dog", "Хот-дог", "hot dog", 250, 9.0, 14.0, 22.0),
    ("nachos_plate", "Начос", "nachos plate", 300, 8.0, 16.0, 32.0),
    ("queso", "Кесо", "queso dip", 180, 7.0, 14.0, 6.0),
    ("spring_rolls_fried", "Спринг-роллы жареные", "fried spring rolls", 230, 6.0, 12.0, 26.0),
    ("miso_soup_bowl", "Мисо-суп", "miso soup", 40, 3.0, 1.2, 4.0),
    ("edamame_bowl", "Эдамаме", "edamame", 120, 11.0, 5.0, 9.0),
    ("seaweed_salad_bowl", "Салат из водорослей", "seaweed salad", 70, 1.5, 3.5, 8.0),
]

WIKI_ROOTS = [
    ("Category:Russian_cuisine", 2),
    ("Category:Ukrainian_cuisine", 2),
    ("Category:Belarusian_cuisine", 2),
    ("Category:Georgian_cuisine", 2),
    ("Category:Armenian_cuisine", 2),
    ("Category:Azerbaijani_cuisine", 1),
    ("Category:Kazakhstani_cuisine", 1),
    ("Category:Uzbek_cuisine", 1),
    ("Category:Soviet_cuisine", 1),
    ("Category:Chinese_cuisine", 1),
    ("Category:Japanese_cuisine", 1),
    ("Category:Korean_cuisine", 1),
    ("Category:Indian_cuisine", 1),
    ("Category:Italian_cuisine", 1),
    ("Category:French_cuisine", 1),
    ("Category:Mexican_cuisine", 1),
    ("Category:Thai_cuisine", 1),
    ("Category:Vietnamese_cuisine", 1),
    ("Category:Turkish_cuisine", 1),
    ("Category:Greek_cuisine", 1),
    ("Category:Spanish_cuisine", 1),
    ("Category:American_cuisine", 1),
    ("Category:British_cuisine", 1),
    ("Category:German_cuisine", 1),
    ("Category:Polish_cuisine", 1),
    ("Category:Hungarian_cuisine", 1),
    ("Category:Levantine_cuisine", 1),
    ("Category:Moroccan_cuisine", 1),
    ("Category:Ethiopian_cuisine", 1),
    ("Category:Brazilian_cuisine", 1),
    ("Category:Peruvian_cuisine", 1),
    ("Category:Hawaiian_cuisine", 2),
    ("Category:Desserts", 1),
    ("Category:Soups", 1),
    ("Category:Salads", 1),
    ("Category:Breads", 1),
    ("Category:Noodle_dishes", 1),
    ("Category:Rice_dishes", 1),
    ("Category:Dumplings", 1),
    ("Category:Pies", 1),
    ("Category:Stews", 1),
    ("Category:Street_food", 1),
    ("Category:Breakfast", 1),
]

GLOSSARY = [
    ("chicken", "курица"), ("beef", "говядина"), ("pork", "свинина"), ("lamb", "баранина"),
    ("fish", "рыба"), ("salmon", "лосось"), ("tuna", "тунец"), ("shrimp", "креветки"),
    ("rice", "рис"), ("noodle", "лапша"), ("noodles", "лапша"), ("soup", "суп"),
    ("salad", "салат"), ("bread", "хлеб"), ("cake", "торт"), ("pie", "пирог"),
    ("potato", "картофель"), ("egg", "яйцо"), ("eggs", "яйца"), ("cheese", "сыр"),
    ("fried", "жареный"), ("grilled", "гриль"), ("roast", "запечённый"), ("boiled", "варёный"),
    ("steamed", "паровой"), ("spicy", "острый"), ("sweet", "сладкий"), ("sour", "кислый"),
    ("dumpling", "пельмени"), ("dumplings", "пельмени"), ("pancake", "блин"), ("pancakes", "блины"),
    ("sauce", "соус"), ("vegetable", "овощи"), ("vegetables", "овощи"), ("mushroom", "грибы"),
    ("tofu", "тофу"), ("curry", "карри"), ("stew", "рагу"), ("porridge", "каша"),
    ("sandwich", "сэндвич"), ("burger", "бургер"), ("pizza", "пицца"), ("pasta", "паста"),
    ("sushi", "суши"), ("sashimi", "сашими"), ("roll", "ролл"), ("bowl", "боул"),
    ("with", "с"), ("and", "и"), ("style", ""), ("homemade", "домашний"),
]


def log(msg: str) -> None:
    print(msg, flush=True)


def get(url: str, timeout: int = 30) -> bytes:
    import urllib.error
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (429, 503, 502):
                raise
            time.sleep(1.5 + attempt * 2)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.4 + attempt)
    raise last


def wiki_api(params: dict) -> dict:
    time.sleep(0.25)
    params = dict(params)
    params.setdefault("format", "json")
    params.setdefault("maxlag", "5")
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)
    return json.loads(get(url, 40).decode("utf-8"))


def slug(text: str) -> str:
    text = text.lower().strip()
    text = text.replace("ё", "е")
    out = []
    for ch in text:
        if "a" <= ch <= "z" or "0" <= ch <= "9":
            out.append(ch)
        elif "а" <= ch <= "я":
            out.append(ch)
        else:
            out.append("_")
    s = re.sub(r"_+", "_", "".join(out)).strip("_")
    return (s or "dish")[:72]


def glossary_ru(en: str) -> str:
    words = re.split(r"[^A-Za-z]+", en.lower())
    mapped = []
    hit = False
    table = dict(GLOSSARY)
    for w in words:
        if not w:
            continue
        if w in table and table[w]:
            mapped.append(table[w])
            hit = True
        elif w in table:
            continue
        else:
            mapped.append(w)
    if not hit:
        return ""
    return " ".join(mapped).strip()


def nutrition_guess(name: str) -> dict:
    text = name.lower()
    rules = [
        (r"cake|cookie|brownie|dessert|торт|печень", 360, 5, 16, 48),
        (r"ice cream|морожен", 207, 3.5, 11, 24),
        (r"soup|борщ|щи|суп|chowder|солянка|уха", 52, 2.6, 2.0, 5.2),
        (r"salad|салат", 120, 3, 8, 8),
        (r"dumpling|пельмен|вареник|хинкали|мант", 220, 10, 9, 24),
        (r"shawarma|шаурм|doner|донер|kebab|кебаб|шашлык", 210, 14, 12, 10),
        (r"pilaf|плов|biryani|paella|risotto|ризотто", 170, 6, 6, 22),
        (r"pancake|блин|оладь|сырник|waffle|вафл", 220, 7, 9, 28),
        (r"porridge|каша|овсян|congee", 90, 3, 2, 14),
        (r"noodle|лапша|ramen|рамен|pho|pasta|паста", 145, 5, 4, 22),
        (r"sushi|суши|poke|поке", 140, 8, 4, 18),
        (r"pizza|пицц", 250, 11, 10, 30),
        (r"burger|бургер", 250, 13, 12, 22),
        (r"bread|хлеб|наан|пирож|самса|croissant", 280, 8, 9, 42),
        (r"fish|рыб|salmon|лосос|tuna|тунец", 150, 20, 6, 1),
        (r"chicken|кур|цыпл", 190, 20, 11, 2),
        (r"beef|говя|steak|стейк", 200, 22, 12, 1),
        (r"pork|свин", 230, 18, 16, 1),
        (r"potato|карто", 95, 2, 2, 16),
        (r"fruit|фрукт|berry", 55, 0.7, 0.3, 13),
    ]
    for pattern, kcal, protein, fat, carbs in rules:
        if re.search(pattern, text):
            return {
                "kcal": kcal, "protein": protein, "fat": fat, "carbs": carbs,
                "nutrition": "estimated",
            }
    return {"kcal": 180, "protein": 8, "fat": 8, "carbs": 16, "nutrition": "estimated"}


def load_food101_nutrition() -> dict:
    path = os.path.join(ROOT, "data", "nutrition.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["items"]


def load_labels() -> list:
    with open(os.path.join(ROOT, "data", "labels.json"), encoding="utf-8") as fh:
        return json.load(fh)


def load_food2k() -> list[str]:
    with open("/tmp/food2k.json", encoding="utf-8") as fh:
        data = json.load(fh)
    names = []
    for key, value in data.items():
        if key.lower().endswith("index"):
            continue
        names.append(str(value))
    return names


def category_members(title: str, cmtype: str) -> list[str]:
    out = []
    cont = None
    while True:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": title,
            "cmtype": cmtype,
            "cmlimit": "500",
        }
        if cont:
            params["cmcontinue"] = cont
        try:
            data = wiki_api(params)
        except Exception as exc:  # noqa: BLE001
            log(f"  категория {title} пропущена: {exc}")
            break
        for row in data.get("query", {}).get("categorymembers", []):
            out.append(row["title"])
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            break
        time.sleep(0.05)
    return out


def collect_titles(limit: int = 10000) -> list[str]:
    pages = []
    seen = set()
    cats_seen = set()

    def add_pages(titles):
        for title in titles:
            if title.startswith("Category:") or title.startswith("List of"):
                continue
            if SKIP_CAT.search(title):
                continue
            if title in seen:
                continue
            seen.add(title)
            pages.append(title)
            if len(pages) >= limit:
                return True
        return False

    for root, depth in WIKI_ROOTS:
        if len(pages) >= limit:
            break
        if root in cats_seen:
            continue
        cats_seen.add(root)
        log(f"вики {root}")
        direct = category_members(root, "page")
        if add_pages(direct):
            break
        if depth < 1:
            continue
        subs = category_members(root, "subcat")
        for sub in subs:
            if len(pages) >= limit:
                break
            if sub in cats_seen or SKIP_CAT.search(sub):
                continue
            cats_seen.add(sub)
            if add_pages(category_members(sub, "page")):
                break
            if depth < 2:
                continue
            for sub2 in category_members(sub, "subcat"):
                if len(pages) >= limit:
                    break
                if sub2 in cats_seen or SKIP_CAT.search(sub2):
                    continue
                cats_seen.add(sub2)
                if add_pages(category_members(sub2, "page")):
                    break
    log(f"статей вики: {len(pages)}")
    return pages


def page_details(titles: list[str]) -> list[dict]:
    rows = []
    for i in range(0, len(titles), 40):
        chunk = titles[i : i + 40]
        try:
            data = wiki_api({
                "action": "query",
                "prop": "pageimages|langlinks",
                "titles": "|".join(chunk),
                "piprop": "thumbnail",
                "pithumbsize": "360",
                "lllang": "ru",
                "redirects": "1",
            })
        except Exception as exc:  # noqa: BLE001
            log(f"  карточки {i}: {exc}")
            continue
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            if page.get("missing") is not None:
                continue
            thumb = (page.get("thumbnail") or {}).get("source")
            ru = ""
            for link in page.get("langlinks") or []:
                if link.get("lang") == "ru":
                    ru = link.get("*") or link.get("langname") or ""
            rows.append({
                "en": page.get("title") or "",
                "ru": ru,
                "thumb": thumb or "",
            })
        if i % 400 == 0:
            log(f"  карточки {i}/{len(titles)}")
        time.sleep(0.05)
    return rows


def build_catalog() -> list[dict]:
    labels = load_labels()
    usda = load_food101_nutrition()
    by_id = {}

    def add(item: dict, replace: bool = False) -> None:
        key = item["id"]
        if key in by_id and not replace:
            prev = by_id[key]
            if item.get("priority", 0) > prev.get("priority", 0):
                by_id[key] = item
            return
        by_id[key] = item

    for idx, row in enumerate(labels):
        nut = usda.get(row["id"], {})
        add({
            "id": row["id"],
            "ru": row["ru"],
            "en": row["en"],
            "kcal": nut.get("kcal", 180),
            "protein": nut.get("protein", 8),
            "fat": nut.get("fat", 8),
            "carbs": nut.get("carbs", 16),
            "nutrition": "usda" if nut else "estimated",
            "priority": 80,
            "classifierIndex": idx,
            "query": row["en"] + " food",
        }, replace=True)

    for dish_id, ru, query, kcal, protein, fat, carbs in COMMON:
        idx = next((i for i, row in enumerate(labels) if row["id"] == dish_id), None)
        add({
            "id": dish_id,
            "ru": ru,
            "en": query,
            "kcal": kcal,
            "protein": protein,
            "fat": fat,
            "carbs": carbs,
            "nutrition": "curated",
            "priority": 100,
            "classifierIndex": idx,
            "query": query,
        }, replace=True)

    for name in load_food2k():
        dish_id = slug(name)
        if not dish_id:
            continue
        nut = nutrition_guess(name)
        ru = glossary_ru(name) or name
        add({
            "id": dish_id,
            "ru": ru,
            "en": name,
            "priority": 40,
            "classifierIndex": None,
            "query": name + " food",
            "ru_source": "glossary" if glossary_ru(name) else "en",
            **nut,
        })

    titles = collect_titles(9000)
    for row in page_details(titles):
        dish_id = slug(row["en"])
        if not dish_id or not row["en"]:
            continue
        nut = nutrition_guess(row["en"] + " " + row["ru"])
        ru = row["ru"] or glossary_ru(row["en"]) or row["en"]
        item = {
            "id": dish_id,
            "ru": ru,
            "en": row["en"],
            "priority": 30,
            "classifierIndex": None,
            "query": row["en"] + " dish",
            "thumb": row["thumb"],
            "ru_source": "wikipedia" if row["ru"] else ("glossary" if glossary_ru(row["en"]) else "en"),
            **nut,
        }
        # Не затираем кураторские и Food-101.
        if dish_id not in by_id:
            add(item)
        elif by_id[dish_id].get("priority", 0) <= 30 and row["thumb"]:
            by_id[dish_id]["thumb"] = row["thumb"]
            if row["ru"]:
                by_id[dish_id]["ru"] = row["ru"]
                by_id[dish_id]["ru_source"] = "wikipedia"

    catalog = sorted(by_id.values(), key=lambda r: (-r.get("priority", 0), r["id"]))
    # Ровно не раздуваем сверх разумного: имена оставляем, картинки ограничит загрузчик.
    log(f"каталог блюд: {len(catalog)}")
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "catalog.json"), "w", encoding="utf-8") as fh:
        json.dump(catalog, fh, ensure_ascii=False)
    return catalog


def save_image(raw: bytes, folder: str, digest: str) -> bool:
    if len(raw) < 4000 or len(raw) > 8_000_000:
        return False
    path = os.path.join(folder, digest[:16] + ".jpg")
    if os.path.exists(path):
        return False
    tmp = path + ".part"
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        if min(img.size) < 80:
            return False
        img.thumbnail((360, 360))
        img.save(tmp, "JPEG", quality=80)
        os.replace(tmp, path)
        return True
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        return False


def download_one(url: str, folder: str, exclude: set[str]) -> bool:
    try:
        raw = get(url, 15)
    except Exception:
        return False
    digest = hashlib.md5(raw).hexdigest()
    if digest in exclude:
        return False
    exclude.add(digest)
    return save_image(raw, folder, digest)


def ddg_urls(query: str, n: int) -> list[str]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    with DDGS() as ddgs:
        rows = list(ddgs.images(query, max_results=n, safesearch="moderate"))
    urls = []
    for row in rows:
        url = row.get("image") or row.get("url")
        if url and url.startswith("http"):
            urls.append(url)
    return urls


def held_out_md5() -> set[str]:
    path = "/tmp/poke_holdout.jpg"
    if not os.path.exists(path):
        return set()
    return {hashlib.md5(open(path, "rb").read()).hexdigest()}


def count_images() -> int:
    total = 0
    for root, _dirs, files in os.walk(OUT):
        total += sum(1 for name in files if name.endswith(".jpg"))
    return total


def download_common(catalog: list[dict], exclude: set[str]) -> str:
    t0 = time.time()
    reason = "common-done"
    common = [row for row in catalog if row.get("priority", 0) >= 100]
    log(f"поиск картинок для частых блюд: {len(common)}")
    for row in common:
        if (time.time() - t0) / 60 > MAX_DDG_MINUTES:
            reason = "ddg-time-cap"
            break
        if count_images() >= MAX_IMAGES:
            reason = "image-cap"
            break
        folder = os.path.join(OUT, row["id"])
        os.makedirs(folder, exist_ok=True)
        have = len([n for n in os.listdir(folder) if n.endswith(".jpg")])
        if have >= TARGET_COMMON:
            continue
        try:
            urls = ddg_urls(row["query"], TARGET_COMMON + 12)
        except Exception as exc:  # noqa: BLE001
            log(f"  поиск {row['id']}: {exc}")
            time.sleep(2)
            continue
        need = TARGET_COMMON - have
        ok = 0
        with ThreadPoolExecutor(max_workers=12) as pool:
            futs = [pool.submit(download_one, url, folder, exclude) for url in urls[: need + 20]]
            for fut in as_completed(futs):
                if fut.result():
                    ok += 1
        log(f"  {row['id']}: +{ok}")
        time.sleep(0.4)
    return reason


def download_thumbs(catalog: list[dict], exclude: set[str]) -> str:
    reason = "thumbs-done"
    jobs = []
    for row in catalog:
        thumb = row.get("thumb")
        if not thumb:
            continue
        folder = os.path.join(OUT, row["id"])
        os.makedirs(folder, exist_ok=True)
        have = [n for n in os.listdir(folder) if n.endswith(".jpg")]
        if have:
            continue
        jobs.append((thumb, folder))
    log(f"миниатюры вики без своих фото: {len(jobs)}")
    done = 0
    with ThreadPoolExecutor(max_workers=16) as pool:
        futs = []
        for url, folder in jobs:
            if count_images() + len(futs) >= MAX_IMAGES:
                reason = "image-cap"
                break
            futs.append(pool.submit(download_one, url, folder, exclude))
        for fut in as_completed(futs):
            if fut.result():
                done += 1
            if done % 500 == 0 and done:
                log(f"  миниатюры {done}")
    log(f"миниатюр сохранено {done}")
    return reason


def write_status(catalog: list[dict], reason: str) -> None:
    classes = 0
    images = 0
    for name in os.listdir(OUT):
        folder = os.path.join(OUT, name)
        if not os.path.isdir(folder):
            continue
        n = len([f for f in os.listdir(folder) if f.endswith(".jpg")])
        if n:
            classes += 1
            images += n
    status = {
        "catalog": len(catalog),
        "classes_with_images": classes,
        "images": images,
        "stop": reason,
        "max_images": MAX_IMAGES,
    }
    with open(os.path.join(OUT, "STATUS.json"), "w", encoding="utf-8") as fh:
        json.dump(status, fh, ensure_ascii=False, indent=2)
    log("STATUS " + json.dumps(status, ensure_ascii=False))


def main():
    os.makedirs(OUT, exist_ok=True)
    exclude = held_out_md5()
    catalog = build_catalog()
    reason = download_common(catalog, exclude)
    if reason == "common-done" and count_images() < MAX_IMAGES:
        reason = download_thumbs(catalog, exclude)
    write_status(catalog, reason)


if __name__ == "__main__":
    main()
