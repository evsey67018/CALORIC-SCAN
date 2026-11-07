#!/usr/bin/env python3
"""Собрать data/labels.json и data/nutrition.json.

Макросы: USDA FoodData Central (SR Legacy и FNDDS, значения на 100 г).
Если готового блюда в базе нет или ответ не проходит проверку, остаётся
ручная оценка по типичным карточкам USDA для этого блюда.
Ключ API: переменная USDA_API_KEY, иначе DEMO_KEY.
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = "/tmp/usda_fdc_cache.json"
API = "https://api.nal.usda.gov/fdc/v1/foods/search"
KEY = os.environ.get("USDA_API_KEY", "DEMO_KEY")

# id, ru, en, portion_g, query, must_all, must_any, avoid, prefer,
# fallback (kcal, protein, fat, carbs, fiber, sugar) на 100 г
FOODS = [
    ("apple_pie", "Яблочный пирог", "Apple pie", 125, "apple pie", ["apple", "pie"], [], ["filling", "turnover", "strudel", "fried"], ["commercially", "nfs"], (237, 1.9, 11.0, 34.0, 1.6, 15.0)),
    ("baby_back_ribs", "Свиные рёбрышки", "Baby back ribs", 250, "pork ribs cooked", ["pork", "rib"], [], ["raw", "sauce", "barbecue"], ["cooked", "roasted", "braised"], (292, 22.8, 21.5, 0.0, 0.0, 0.0)),
    ("baklava", "Пахлава", "Baklava", 80, "baklava", ["baklava"], [], [], [], (440, 6.6, 29.3, 37.6, 2.1, 20.0)),
    ("beef_carpaccio", "Карпаччо из говядины", "Beef carpaccio", 90, "beef carpaccio", ["carpaccio"], [], [], ["beef"], (159, 22.0, 7.5, 0.6, 0.0, 0.2)),
    ("beef_tartare", "Тартар из говядины", "Beef tartare", 120, "beef tartare", ["tartare"], ["beef", "steak"], [], [], (192, 16.8, 13.2, 1.4, 0.2, 0.8)),
    ("beet_salad", "Свекольный салат", "Beet salad", 150, "beet salad", ["beet", "salad"], [], ["greens only"], [], (65, 1.6, 2.4, 9.2, 2.2, 7.0)),
    ("beignets", "Бенье", "Beignets", 90, "beignet", [], ["beignet"], [], [], (370, 6.2, 17.5, 47.0, 1.3, 16.0)),
    ("bibimbap", "Пибимпап", "Bibimbap", 450, "bibimbap", ["bibimbap"], [], [], [], (129, 6.4, 3.9, 16.8, 2.0, 3.2)),
    ("bread_pudding", "Хлебный пудинг", "Bread pudding", 170, "bread pudding", ["bread", "pudding"], [], ["mix"], [], (180, 5.4, 6.8, 25.5, 1.0, 13.5)),
    ("breakfast_burrito", "Утреннее буррито", "Breakfast burrito", 280, "breakfast burrito", ["burrito"], ["breakfast", "egg"], [], [], (196, 9.2, 8.8, 18.6, 1.8, 2.2)),
    ("bruschetta", "Брускетта", "Bruschetta", 100, "bruschetta", ["bruschetta"], [], [], [], (189, 5.6, 7.4, 25.0, 1.8, 3.1)),
    ("caesar_salad", "Салат Цезарь", "Caesar salad", 220, "caesar salad", ["caesar", "salad"], [], ["salad dressing", "no dressing", "crouton"], ["romaine"], (187, 7.2, 14.5, 7.4, 1.6, 1.8)),
    ("cannoli", "Канноли", "Cannoli", 75, "cannoli", ["cannoli"], [], [], [], (366, 7.4, 18.2, 43.5, 1.1, 22.0)),
    ("caprese_salad", "Салат Капрезе", "Caprese salad", 180, "caprese salad", ["caprese"], [], [], [], (166, 8.2, 12.4, 3.6, 0.8, 2.4)),
    ("carrot_cake", "Морковный торт", "Carrot cake", 110, "carrot cake", ["carrot", "cake"], [], ["mix", "candy"], [], (415, 4.4, 21.0, 52.0, 1.6, 34.0)),
    ("ceviche", "Севиче", "Ceviche", 160, "ceviche", ["ceviche"], [], [], [], (95, 13.5, 2.2, 5.4, 0.7, 1.6)),
    ("cheese_plate", "Сырная тарелка", "Cheese plate", 90, "cheese cheddar", ["cheese"], [], ["sauce", "sandwich", "puff", "cake", "macaroni", "pizza", "spread", "cracker", "soup", "cottage", "ricotta"], ["cheddar"], (403, 22.9, 33.3, 3.1, 0.0, 0.5)),
    ("cheesecake", "Чизкейк", "Cheesecake", 125, "cheesecake", ["cheesecake"], [], ["mix", "candy"], [], (321, 5.5, 22.5, 25.5, 0.4, 21.0)),
    ("chicken_curry", "Курица карри", "Chicken curry", 350, "chicken curry", ["chicken", "curry"], [], ["powder", "sauce only", "paste"], [], (149, 11.2, 8.4, 6.8, 1.4, 2.4)),
    ("chicken_quesadilla", "Кесадилья с курицей", "Chicken quesadilla", 180, "chicken quesadilla", ["quesadilla"], ["chicken"], [], [], (287, 15.4, 14.2, 22.5, 1.4, 1.6)),
    ("chicken_wings", "Куриные крылышки", "Chicken wings", 170, "chicken wings cooked", ["chicken", "wing"], [], ["raw", "sauce only"], ["fried", "roasted", "cooked"], (290, 26.0, 19.5, 0.0, 0.0, 0.0)),
    ("chocolate_cake", "Шоколадный торт", "Chocolate cake", 100, "chocolate cake", ["chocolate", "cake"], [], ["mix", "frosting only", "candy", "cookie", "snack", "flourless"], [], (389, 3.6, 18.5, 53.0, 1.8, 38.0)),
    ("chocolate_mousse", "Шоколадный мусс", "Chocolate mousse", 110, "chocolate mousse", ["chocolate", "mousse"], [], [], [], (225, 4.2, 13.5, 22.0, 1.4, 18.5)),
    ("churros", "Чуррос", "Churros", 80, "churro", [], ["churro"], [], [], (377, 4.8, 18.6, 48.5, 1.6, 15.0)),
    ("clam_chowder", "Суп из моллюсков", "Clam chowder", 300, "clam chowder", ["clam", "chowder"], [], [], ["new england"], (84, 3.8, 3.6, 8.4, 0.6, 1.4)),
    ("club_sandwich", "Клаб-сэндвич", "Club sandwich", 230, "club sandwich", ["club", "sandwich"], [], [], [], (234, 13.5, 11.2, 18.4, 1.2, 3.2)),
    ("crab_cakes", "Крабовые котлеты", "Crab cakes", 150, "crab cake", ["crab", "cake"], [], ["mix"], [], (201, 13.8, 10.2, 12.6, 0.5, 1.1)),
    ("creme_brulee", "Крем-брюле", "Crème brûlée", 120, "creme brulee", [], ["brulee", "creme brulee"], [], [], (267, 4.4, 17.8, 22.4, 0.0, 21.0)),
    ("croque_madame", "Крок-мадам", "Croque madame", 200, "croque madame", [], ["croque"], [], ["madame"], (292, 15.6, 16.8, 18.2, 1.0, 2.6)),
    ("cup_cakes", "Капкейки", "Cupcakes", 75, "cupcake chocolate", [], ["cupcake"], ["mix"], [], (369, 4.1, 14.8, 55.0, 0.9, 39.0)),
    ("deviled_eggs", "Фаршированные яйца", "Deviled eggs", 90, "deviled egg", ["egg"], ["deviled"], [], [], (188, 10.6, 15.2, 1.8, 0.1, 0.9)),
    ("donuts", "Пончики", "Donuts", 70, "yeast doughnut", [], ["doughnut", "donut"], ["hole"], ["yeast", "nfs"], (421, 5.0, 22.7, 48.9, 1.7, 20.5)),
    ("dumplings", "Дамплинги", "Dumplings", 180, "dumpling steamed", [], ["dumpling"], ["potsticker wrapper mix"], ["steamed", "meat", "nfs"], (164, 6.8, 4.2, 25.4, 1.2, 1.4)),
    ("edamame", "Эдамаме", "Edamame", 120, "edamame cooked", ["edamame"], [], [], ["cooked"], (122, 11.9, 5.2, 9.9, 5.2, 2.2)),
    ("eggs_benedict", "Яйца Бенедикт", "Eggs Benedict", 220, "eggs benedict", ["benedict"], [], [], [], (240, 12.4, 15.6, 12.2, 0.6, 1.6)),
    ("escargots", "Улитки", "Escargots", 90, "escargot cooked", [], ["escargot", "snail"], ["raw"], ["cooked"], (90, 16.1, 1.4, 2.0, 0.0, 0.0)),
    ("falafel", "Фалафель", "Falafel", 140, "falafel", ["falafel"], [], ["mix"], [], (333, 13.3, 17.8, 31.8, 4.9, 1.8)),
    ("filet_mignon", "Филе-миньон", "Filet mignon", 200, "beef tenderloin cooked", ["beef", "tenderloin"], [], ["raw"], ["grilled", "broiled", "roasted"], (196, 28.0, 8.6, 0.0, 0.0, 0.0)),
    ("fish_and_chips", "Рыба с картофелем фри", "Fish and chips", 380, "fish and chips", ["fish"], ["chip"], [], [], (232, 9.8, 12.4, 20.2, 1.6, 0.6)),
    ("foie_gras", "Фуа-гра", "Foie gras", 50, "foie gras", [], ["foie", "goose liver"], [], ["duck", "goose"], (462, 11.4, 43.8, 4.7, 0.0, 0.0)),
    ("french_fries", "Картофель фри", "French fries", 117, "french fries", ["french"], ["fry", "fried"], ["sweet"], ["nfs", "fast"], (312, 3.4, 15.0, 41.4, 3.8, 0.3)),
    ("french_onion_soup", "Французский луковый суп", "French onion soup", 300, "french onion soup", ["onion", "soup"], [], ["mix", "dry"], ["french"], (56, 3.2, 2.4, 6.8, 0.8, 3.2)),
    ("french_toast", "Французские тосты", "French toast", 150, "french toast", ["french", "toast"], [], ["stick", "cinnamon only"], [], (229, 7.6, 9.0, 28.4, 1.3, 7.8)),
    ("fried_calamari", "Жареные кальмары", "Fried calamari", 150, "fried squid", [], ["squid", "calamari"], ["raw"], ["fried"], (175, 15.2, 7.4, 12.2, 0.3, 0.4)),
    ("fried_rice", "Жареный рис", "Fried rice", 300, "fried rice", ["fried", "rice"], [], ["seasoning", "mix"], ["nfs"], (168, 4.6, 5.2, 25.8, 0.9, 1.2)),
    ("frozen_yogurt", "Замороженный йогурт", "Frozen yogurt", 150, "frozen yogurt", ["frozen", "yogurt"], [], ["bar", "cone"], ["vanilla", "nfs"], (127, 3.0, 3.6, 21.6, 0.0, 19.5)),
    ("garlic_bread", "Чесночный хлеб", "Garlic bread", 70, "garlic bread", ["garlic", "bread"], [], [], [], (350, 8.4, 15.6, 44.8, 2.1, 2.6)),
    ("gnocchi", "Ньокки", "Gnocchi", 280, "gnocchi", ["gnocchi"], [], ["mix"], [], (148, 4.0, 1.6, 30.4, 1.8, 1.0)),
    ("greek_salad", "Греческий салат", "Greek salad", 220, "greek salad", ["greek", "salad"], [], ["dressing only"], [], (106, 2.8, 8.1, 5.4, 1.6, 3.0)),
    ("grilled_cheese_sandwich", "Сэндвич с сыром", "Grilled cheese sandwich", 130, "grilled cheese sandwich", ["grilled", "cheese"], [], [], ["sandwich"], (326, 12.4, 16.8, 30.5, 1.4, 4.2)),
    ("grilled_salmon", "Лосось на гриле", "Grilled salmon", 170, "salmon cooked", ["salmon"], [], ["raw", "smoked", "canned", "salad"], ["broiled", "grilled", "cooked"], (208, 22.1, 12.4, 0.0, 0.0, 0.0)),
    ("guacamole", "Гуакамоле", "Guacamole", 70, "guacamole", ["guacamole"], [], [], ["nfs"], (157, 1.9, 14.3, 8.5, 6.7, 0.7)),
    ("gyoza", "Гёдза", "Gyoza", 140, "gyoza", [], ["gyoza", "potsticker"], [], [], (225, 9.4, 8.6, 26.8, 1.4, 2.1)),
    ("hamburger", "Гамбургер", "Hamburger", 200, "hamburger sandwich", [], ["hamburger"], ["helper", "bun only", "patty only"], ["sandwich", "single", "nfs"], (264, 12.9, 10.8, 28.6, 1.3, 5.4)),
    ("hot_and_sour_soup", "Кисло-острый суп", "Hot and sour soup", 300, "hot and sour soup", ["soup"], ["hot and sour", "sour"], [], [], (39, 2.6, 1.4, 4.2, 0.4, 0.8)),
    ("hot_dog", "Хот-дог", "Hot dog", 110, "hot dog", ["hot", "dog"], [], ["sauce only"], ["nfs"], (290, 10.6, 17.6, 22.4, 1.0, 4.6)),
    ("huevos_rancheros", "Яичница по-мексикански", "Huevos rancheros", 280, "huevos rancheros", [], ["huevos", "rancheros"], [], [], (152, 7.4, 8.2, 12.6, 2.4, 2.8)),
    ("hummus", "Хумус", "Hummus", 70, "hummus", ["hummus"], [], ["dessert", "chocolate"], ["commercial", "nfs"], (166, 7.9, 9.6, 14.3, 6.0, 0.3)),
    ("ice_cream", "Мороженое", "Ice cream", 130, "ice cream vanilla", ["ice", "cream"], [], ["cone", "sandwich", "bar", "cake", "soda"], ["vanilla", "nfs"], (207, 3.5, 11.0, 23.6, 0.7, 21.2)),
    ("lasagna", "Лазанья", "Lasagna", 300, "lasagna meat", [], ["lasagna", "lasagne"], ["frozen dinner diet"], ["meat", "nfs"], (135, 8.2, 5.6, 13.4, 1.3, 3.1)),
    ("lobster_bisque", "Биск из омара", "Lobster bisque", 280, "lobster bisque", ["lobster"], ["bisque"], [], [], (106, 5.2, 6.4, 6.8, 0.3, 2.2)),
    ("lobster_roll_sandwich", "Ролл с омаром", "Lobster roll", 220, "lobster roll", ["lobster"], ["roll", "sandwich"], [], [], (230, 14.2, 10.6, 18.8, 0.8, 3.4)),
    ("macaroni_and_cheese", "Макароны с сыром", "Macaroni and cheese", 280, "macaroni and cheese", ["macaroni", "cheese"], [], ["mix dry", "powder"], ["nfs"], (164, 6.8, 6.6, 19.4, 1.0, 3.2)),
    ("macarons", "Макаруны", "Macarons", 36, "macaron cookie", [], ["macaron"], ["macaroni"], [], (405, 6.2, 13.5, 64.0, 1.6, 52.0)),
    ("miso_soup", "Суп мисо", "Miso soup", 250, "miso soup", ["miso"], [], ["paste only", "dry"], ["soup"], (40, 2.2, 1.2, 5.0, 0.5, 1.6)),
    ("mussels", "Мидии", "Mussels", 150, "mussels cooked", [], ["mussel"], ["raw", "sauce", "canned smoked"], ["cooked", "steamed"], (172, 23.8, 4.5, 7.4, 0.0, 0.0)),
    ("nachos", "Начос", "Nachos", 180, "nachos cheese", ["nachos"], [], ["sauce only", "chip only"], ["cheese"], (306, 8.2, 17.4, 31.2, 3.2, 1.8)),
    ("omelette", "Омлет", "Omelette", 160, "omelet plain", [], ["omelet", "omelette"], ["mix"], ["plain", "nfs"], (154, 10.6, 11.7, 0.7, 0.0, 0.4)),
    ("onion_rings", "Луковые кольца", "Onion rings", 120, "onion rings", ["onion", "ring"], [], [], ["fried", "nfs"], (332, 4.5, 16.8, 41.2, 2.4, 4.6)),
    ("oysters", "Устрицы", "Oysters", 100, "oysters raw", [], ["oyster"], ["sauce", "stew", "fried", "smoked"], ["raw"], (68, 7.1, 2.5, 3.9, 0.0, 0.0)),
    ("pad_thai", "Пад тай", "Pad Thai", 380, "pad thai", ["pad", "thai"], [], [], ["chicken", "nfs"], (160, 7.4, 5.8, 19.6, 1.1, 4.2)),
    ("paella", "Паэлья", "Paella", 400, "paella", ["paella"], [], [], [], (156, 8.6, 4.8, 18.4, 1.0, 1.2)),
    ("pancakes", "Панкейки", "Pancakes", 150, "pancakes plain", [], ["pancake"], ["mix dry"], ["plain", "nfs"], (227, 6.4, 9.7, 28.3, 1.0, 6.2)),
    ("panna_cotta", "Панна-котта", "Panna cotta", 120, "panna cotta", ["panna"], [], [], [], (176, 3.2, 10.4, 17.6, 0.0, 16.8)),
    ("peking_duck", "Утка по-пекински", "Peking duck", 180, "peking duck", ["duck"], ["peking", "roasted"], [], ["roasted", "peking"], (337, 19.0, 28.4, 0.4, 0.0, 0.0)),
    ("pho", "Фо", "Pho", 500, "pho", ["pho"], [], ["seasoning"], ["beef", "nfs"], (67, 4.8, 1.6, 8.2, 0.4, 1.2)),
    ("pizza", "Пицца", "Pizza", 200, "cheese pizza", ["pizza"], [], ["topping", "sauce", "crust", "dough", "roll", "bagel"], ["cheese", "nfs"], (266, 11.4, 9.7, 33.3, 2.3, 3.6)),
    ("pork_chop", "Свиная отбивная", "Pork chop", 170, "pork chop cooked", ["pork"], ["chop", "loin"], ["raw"], ["broiled", "cooked", "pan"], (231, 25.7, 13.6, 0.0, 0.0, 0.0)),
    ("poutine", "Путин", "Poutine", 400, "poutine", ["poutine"], [], [], [], (235, 8.4, 12.6, 21.8, 1.8, 2.2)),
    ("prime_rib", "Прайм-риб", "Prime rib", 240, "beef rib roast cooked", ["beef", "rib"], ["roast", "prime"], ["raw"], ["roasted", "prime"], (338, 21.5, 27.6, 0.0, 0.0, 0.0)),
    ("pulled_pork_sandwich", "Сэндвич с томлёной свининой", "Pulled pork sandwich", 240, "pulled pork sandwich", ["pulled", "pork"], [], [], ["sandwich"], (229, 12.2, 7.8, 26.4, 1.6, 8.4)),
    ("ramen", "Рамен", "Ramen", 550, "ramen soup", ["ramen"], [], ["seasoning dry", "noodle dry"], ["soup", "nfs"], (86, 4.2, 2.8, 10.6, 0.6, 1.0)),
    ("ravioli", "Равиоли", "Ravioli", 280, "ravioli meat", [], ["ravioli"], ["unprepared"], ["meat", "tomato", "nfs"], (157, 6.6, 5.2, 20.8, 1.4, 2.2)),
    ("red_velvet_cake", "Торт «Красный бархат»", "Red velvet cake", 110, "red velvet cake", ["red", "velvet"], [], ["mix"], ["cake"], (368, 4.2, 16.4, 52.5, 0.7, 38.0)),
    ("risotto", "Ризотто", "Risotto", 300, "risotto", ["risotto"], [], [], [], (166, 4.2, 5.4, 24.6, 0.7, 0.8)),
    ("samosa", "Самоса", "Samosa", 100, "samosa", ["samosa"], [], [], [], (262, 5.2, 13.4, 29.8, 3.1, 1.8)),
    ("sashimi", "Сашими", "Sashimi", 110, "sashimi", ["sashimi"], [], [], ["tuna", "nfs"], (127, 20.4, 4.2, 0.4, 0.0, 0.0)),
    ("scallops", "Морские гребешки", "Scallops", 130, "scallops cooked", [], ["scallop"], ["raw", "fried"], ["steamed", "cooked"], (111, 20.5, 1.4, 5.4, 0.0, 0.0)),
    ("seaweed_salad", "Салат из водорослей", "Seaweed salad", 100, "seaweed salad", ["seaweed"], ["salad"], [], [], (70, 1.4, 3.6, 8.2, 1.2, 5.4)),
    ("shrimp_and_grits", "Креветки с кукурузной кашей", "Shrimp and grits", 350, "shrimp and grits", ["shrimp", "grits"], [], [], [], (142, 8.8, 5.6, 13.8, 0.5, 0.8)),
    ("spaghetti_bolognese", "Спагетти болоньезе", "Spaghetti Bolognese", 400, "spaghetti meat sauce", ["spaghetti"], ["meat sauce", "bolognese"], [], ["meat"], (132, 6.8, 4.2, 16.6, 1.6, 3.4)),
    ("spaghetti_carbonara", "Спагетти карбонара", "Spaghetti carbonara", 350, "spaghetti carbonara", [], ["carbonara"], [], [], (176, 7.8, 7.6, 18.4, 1.0, 1.4)),
    ("spring_rolls", "Спринг-роллы", "Spring rolls", 140, "spring roll", ["spring", "roll"], [], ["wrapper only", "sauce"], ["fried", "fresh"], (176, 5.2, 6.8, 23.6, 1.6, 2.4)),
    ("steak", "Стейк", "Steak", 220, "beef steak grilled", ["beef", "steak"], [], ["raw", "sauce", "fish"], ["grilled", "broiled", "nfs"], (271, 25.4, 18.0, 0.0, 0.0, 0.0)),
    ("strawberry_shortcake", "Клубничный бисквит", "Strawberry shortcake", 150, "strawberry shortcake", ["strawberry", "shortcake"], [], [], [], (283, 4.2, 11.6, 41.2, 1.3, 24.5)),
    ("sushi", "Суши", "Sushi", 180, "sushi", ["sushi"], [], ["sashimi", "sauce"], ["nfs"], (143, 5.8, 2.2, 24.6, 1.0, 3.4)),
    ("tacos", "Тако", "Tacos", 180, "beef taco", [], ["taco"], ["sauce", "shell only", "salad"], ["beef", "nfs"], (226, 10.8, 12.4, 17.6, 2.4, 1.6)),
    ("takoyaki", "Такояки", "Takoyaki", 160, "takoyaki", ["takoyaki"], [], [], [], (188, 7.2, 7.4, 22.6, 0.6, 2.2)),
    ("tiramisu", "Тирамису", "Tiramisu", 140, "tiramisu", ["tiramisu"], [], [], [], (283, 4.8, 15.6, 29.4, 0.6, 21.0)),
    ("tuna_tartare", "Тартар из тунца", "Tuna tartare", 120, "tuna tartare", ["tuna", "tartare"], [], [], [], (148, 18.6, 6.4, 2.8, 0.2, 1.2)),
    ("waffles", "Вафли", "Waffles", 150, "waffle plain", [], ["waffle"], ["mix dry", "cone"], ["plain", "nfs"], (291, 6.8, 14.1, 32.8, 1.6, 8.4)),
]


def fold(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text


def has_term(text: str, term: str) -> bool:
    term = fold(term)
    return re.search(r"(?<![a-z])" + re.escape(term) + r"s?(?![a-z])", text) is not None


def nutrients(food: dict) -> dict:
    found = {}
    for nut in food.get("foodNutrients", []):
        nid = nut.get("nutrientId")
        val = nut.get("value")
        if val is None:
            continue
        if nid == 1008:
            found["kcal"] = float(val)
        elif nid == 1062:
            found["kj"] = float(val)
        elif nid == 1003:
            found["protein"] = float(val)
        elif nid == 1004:
            found["fat"] = float(val)
        elif nid == 1005:
            found["carbs"] = float(val)
        elif nid == 1079:
            found["fiber"] = float(val)
        elif nid in (2000, 1063) and "sugar" not in found:
            found["sugar"] = float(val)
    if "kcal" not in found and "kj" in found:
        found["kcal"] = found["kj"] / 4.184
    return found


def acceptable(nuts: dict) -> bool:
    kcal = nuts.get("kcal")
    protein = nuts.get("protein")
    fat = nuts.get("fat")
    carbs = nuts.get("carbs")
    if kcal is None or protein is None or fat is None or carbs is None:
        return False
    if not (12 <= kcal <= 760):
        return False
    if min(protein, fat, carbs) < 0 or protein > 50 or fat > 55 or carbs > 90:
        return False
    atwater = 4 * protein + 9 * fat + 4 * carbs
    if abs(atwater - kcal) > max(80, 0.55 * kcal):
        return False
    return True


def score_food(food, must_all, must_any, avoid, prefer) -> float | None:
    desc = fold(food.get("description", ""))
    dtype = food.get("dataType", "")
    if "branded" in dtype.lower():
        return None
    nuts = nutrients(food)
    if not acceptable(nuts):
        return None
    for term in must_all:
        if not has_term(desc, term):
            return None
    if must_any and not any(has_term(desc, term) for term in must_any):
        return None
    for term in avoid:
        if has_term(desc, term):
            return None
    score = 8.0
    if "survey" in dtype.lower():
        score += 4
    if "sr legacy" in dtype.lower():
        score += 2
    if "nfs" in desc:
        score += 4
    for term in prefer:
        if has_term(desc, term):
            score += 5
    score -= len(desc) * 0.015
    return score


def load_cache() -> dict:
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_cache(cache: dict) -> None:
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(cache, fh)


def search(query: str, cache: dict) -> list:
    if query in cache:
        return cache[query]
    if os.environ.get("USDA_OFFLINE") == "1":
        return []
    params = [
        ("api_key", KEY),
        ("query", query),
        ("pageSize", "25"),
        ("dataType", "SR Legacy"),
        ("dataType", "Survey (FNDDS)"),
    ]
    url = API + "?" + urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "caloric-scan/1.0"})
            with urllib.request.urlopen(req, timeout=40) as resp:
                payload = json.load(resp)
            foods = payload.get("foods", [])
            cache[query] = foods
            save_cache(cache)
            time.sleep(1.1)
            return foods
        except Exception as exc:  # noqa: BLE001 — сеть USDA капризная, нужен повтор
            last_err = exc
            time.sleep(2.5 * (attempt + 1))
    print(f"  USDA не ответил на «{query}»: {last_err}")
    return []


def round_macro(value):
    if value is None:
        return None
    return round(float(value), 1)


def main():
    cache = load_cache()
    labels = []
    items = {}
    used_usda = 0
    for row in FOODS:
        fid, ru, en, portion, query, must_all, must_any, avoid, prefer, fb = row
        labels.append({"id": fid, "ru": ru, "en": en})
        best = None
        best_score = None
        for food in search(query, cache):
            sc = score_food(food, must_all, must_any, avoid, prefer)
            if sc is None:
                continue
            if best_score is None or sc > best_score:
                best_score = sc
                best = food
        if best is not None:
            nuts = nutrients(best)
            items[fid] = {
                "kcal": int(round(nuts["kcal"])),
                "protein": round_macro(nuts["protein"]),
                "fat": round_macro(nuts["fat"]),
                "carbs": round_macro(nuts["carbs"]),
                "fiber": round_macro(nuts.get("fiber")),
                "sugar": round_macro(nuts.get("sugar")),
                "portion_g": portion,
                "fdc_id": best.get("fdcId"),
                "fdc_description": best.get("description"),
                "data_type": best.get("dataType"),
                "source": "USDA FoodData Central",
            }
            used_usda += 1
            print(f"USDA  {fid:24} {items[fid]['kcal']:4}  {best.get('description')[:70]}")
        else:
            kcal, protein, fat, carbs, fiber, sugar = fb
            items[fid] = {
                "kcal": int(kcal),
                "protein": round_macro(protein),
                "fat": round_macro(fat),
                "carbs": round_macro(carbs),
                "fiber": round_macro(fiber),
                "sugar": round_macro(sugar),
                "portion_g": portion,
                "fdc_id": None,
                "fdc_description": None,
                "data_type": None,
                "source": "curated USDA reference (no clean FDC match)",
            }
            print(f"CUR   {fid:24} {int(kcal):4}")

    if len(labels) != 101 or len(items) != 101:
        raise SystemExit(f"Ожидалось 101 блюдо, собрано {len(items)}")
    order_path = os.path.join(os.path.dirname(__file__), "food101_classes.txt")
    with open(order_path, encoding="utf-8") as fh:
        order = [line.strip() for line in fh if line.strip()]
    by_id = {row["id"]: row for row in labels}
    missing = [name for name in order if name not in by_id]
    extra = [row["id"] for row in labels if row["id"] not in order]
    if missing or extra or len(order) != 101:
        raise SystemExit(f"Расхождение с food101_classes.txt missing={missing} extra={extra}")
    # В classes.txt cheesecake стоит перед cheese_plate — это порядок датасета, не sort().
    labels = [by_id[name] for name in order]

    data_dir = os.path.join(ROOT, "data")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "labels.json"), "w", encoding="utf-8") as fh:
        json.dump(labels, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    payload = {
        "per": "100g",
        "portion_field": "portion_g",
        "note": (
            "Энергия и БЖУ — на 100 г. portion_g — типичная съедаемая порция. "
            "Основной источник: USDA FoodData Central, типы SR Legacy и Survey (FNDDS). "
            "Если у блюда нет чистого совпадения, стоит пометка curated USDA reference: "
            "это ручная сводка по близким карточкам USDA, не выдуманные нули."
        ),
        "items": items,
    }
    with open(os.path.join(data_dir, "nutrition.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"USDA {used_usda}/101, curated {101 - used_usda}")


if __name__ == "__main__":
    main()
