"""Ассистент «Кирилл»: три агента поверх робота подбора.

1. Расшифровщик — знает устройство машины: сумбур клиента → точный список деталей (ось, сколько, «под вопросом»).
2. Подборщик — по каждой детали ищет в каталоге ЭТОЙ машины инструментами робота (дерево групп, векторы, состав
   группы, детали к сборке у поставщиков) и выбирает детали со схемы.
3. Цены — ABCP, как у робота (Engine.offers + curate); проверяющий (код) отсекает чужой товар и подписывает оригинал
   маркой машины.
4. Продавец — варианты под потребность клиента (качество / оптимально / подешевле, срочно или нет) с обоснованием;
   цифры только из данных. «Качество» — уровни брендов из app/data/brand_quality.json (поставщики конвейера), а не
   гарантии магазина.

До подбора — вопросы, как у продавца (руководитель 26.10: «выявлять потребность, три вопроса до подбора»):
«надолго или подешевле», «срочно или можно подождать» и уточнения расшифровщика. Ответы — только на этот подбор,
в следующий раз спрашиваем снова (пользователь 26.10: запоминать не нужно).

Номера и цены модели не придумывают: они видят только то, что вернули инструменты. Модели получают текст без
телефонов и VIN (understand.mask). Лаборатория и замеры — /srv/podbor-lab/runs/2026-10-26-agent (26.10.2026).
"""
import asyncio
import contextvars
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from ..podbor import emb as EM
from ..podbor import knowledge as K
from ..podbor import stm as STM
from ..podbor import article as A
from ..podbor import text as T
from ..podbor.catalog import image_url
from ..podbor.engine import nice_brand
from ..podbor.offers import _desc_head, _offer, curate, days, not_the_part, oem_brand_for, original_keys
from ..podbor.understand import mask
from . import quality as Q
from .models import Gateway, OverBudget

log = logging.getLogger("assistant")
_ON_PART: contextvars.ContextVar = contextvars.ContextVar("kirill_on_part", default=None)   # ответ по частям

MAX_ITEMS = 15          # позиций в одном сообщении: дальше — менеджеру
SPLIT_FROM = 3          # строк-позиций в заявке, с которых расшифровка идёт по строкам (короткую — целиком)
SELECT_STEPS = 7        # шагов подборщика на одну деталь
MAX_SCHEMES = 2         # картинок схем подборщику на одну деталь (одна — около 2 копеек у Haiku)
SCHEME_SIDE = 1100      # сторона схемы для модели, px: номера позиций читаются, токенов немного
HISTORY = 8             # реплик разговора в памяти

ASK_VIN = ("Пришлите, пожалуйста, VIN (17 знаков), номер кузова японских машин (например GX110-1234567) или госномер — "
           "подберу запчасти точно по вашей машине.")
NO_CAR = ("По этому номеру машина в каталоге не нашлась. Проверьте VIN (17 знаков, есть в СТС и ПТС) или пришлите "
          "госномер — либо напишите марку, модель, год и объём двигателя, передам менеджеру.")
FAILED = "Сейчас не получается подобрать автоматически — передал ваш вопрос менеджеру, он ответит здесь же."
NO_QUICK = ("По этой машине каталог не даёт подбор по названиям деталей — передам менеджеру, он подберёт по схемам "
            "вручную и напишет здесь же.")
CONTACT_ASK = "Чтобы менеджер связался и подтвердил заказ, нажмите «📱 Поделиться номером» внизу экрана."
MAX_QUESTIONS = 3       # вопросов до подбора — не больше трёх
CONVERSATION = 30 * 60  # тот же разговор: «надолго или подешевле» не переспрашиваем (дольше — спросим снова)

GOALS = {"quality": "Качество", "optimal": "Оптимально", "cheap": "Подешевле"}
WHEN = {"urgent": "Срочно, 1–2 дня", "wait": "Могу подождать"}
BUTTONS = [[(v, f"goal:{k}") for k, v in GOALS.items()], [(v, f"when:{k}") for k, v in WHEN.items()],
           [("✅ Подобрать", "go")]]

DECODER = """Ты — опытный мастер-приёмщик автосервиса и продавец запчастей. Машина клиента: {car}.
Клиент пишет сумбурно, жаргоном, с сокращениями. Переведи его ПОСЛЕДНЕЕ сообщение (с учётом разговора выше) в точный
список запчастей, как их назвал бы каталог, используя знание устройства автомобиля:
- жаргон → нормальное название (подсказки к этому сообщению — в «Знаниях мастера» ниже, если есть);
- «компл», «комплект», «+ всё что нужно» — раскрой, что обычно меняют вместе. Такие детали помечай certainty=maybe;
- «под вопросом», «по факту», «если надо» — certainty=maybe и note, что проверить при осмотре;
- ось (front/rear) и сколько штук на машину (парные детали обычно 2: левая и правая);
- не деталь (вопрос о цене, сроке, оплате, доставке, приветствие, «спасибо») — в not_parts;
- неясно, какая именно деталь («диски» — тормозные или колёсные?) — вопрос в questions, а не позиция;
- клиент просит именно отдельно, не в сборе («сайлентблоки отдельно», «только шаровую, без рычага») — separate=true;
- фирму детали («колодки nagamochi», «фильтр Mann») — в brand, а в need — только сама деталь;
- в разговоре выше ты уже задавал вопрос, а клиент на него отвечает («диски тормозные») — это уточнение к прошлой
  просьбе: верни позиции с учётом ответа, вопрос не повторяй.
Сначала определи intent — чего клиент хочет ЭТИМ сообщением:
- parts — подобрать детали (новые или уточнение к прошлым);
- order — оформить выбранное из прошлого ответа («оформляй», «беру», «да, всё верно», «оригинал по масляному»,
  «что советуешь — то и оформляй»): items не заполняй, в order — его слова;
- more — показать другие варианты по уже подобранному («а какие есть?», «а получше?», «подешевле есть?», конкретная
  фирма — «зепро», «есть Bosch?»): items не заполняй, фирму — в brand, деталь — в more_of;
- number — клиент прислал номер детали и спрашивает, подойдёт ли («а этот номер 104000HQ?»): номера — в numbers;
- scheme — просит схему, «где стоит», картинку;
- question — вопрос не о подборе (оплата, доставка, срок, магазин) или болтовня.
note пиши клиенту на «вы», не о нём в третьем лице.
В каждой позиции why — одной фразой, как ты понял слова клиента: какое слово во что перевёл и почему (знание мастера,
устройство машины, ответ на вопрос выше). Это для разбора ошибок, клиент его не видит.
Машина уже определена по VIN — VIN, кузов и марку не спрашивай.{notes}
Ответь вызовом decoded."""

FOCUS = """
Сейчас расшифруй ТОЛЬКО эту часть сообщения: «{part}». Всё сообщение — для контекста (ось из заголовков вроде
«Передок:», «Задок:», уточнения рядом). Детали из других строк не добавляй — их разберут отдельно. В этой части нет
детали (заголовок, вопрос, приветствие) — items пустой."""

SELECTOR = """Ты подбираешь одну деталь в каталоге машины {car}. Нужно: {need} (ось: {axis}; клиент писал: «{words}»).{separate}
Ниже уже есть кандидаты из каталога и детали первой группы — если нужная деталь там есть, сразу вызывай pick.
Иначе ищи search_catalog (другие формулировки: жаргон → каталожное название), открывай show_parts; в быстрых группах
нет — ищи search_units (узлы полного каталога; названия бывают английские — ищи и по-английски: «front brake»,
«rear suspension») и открывай узел «u…» show_parts. Выбирай детали,
которые и есть нужная (с нужной осью; левую и правую — обе). Названия и позиции бери только из инструментов.
Мелкие детали (сайлентблок, пыльник, втулка, шаровая, отбойник, подшипник опоры) производитель часто отдельно не продаёт —
только в сборе с рычагом, цапфой, стойкой; у неоригинальных фирм они есть. Если в группе нашлась только сборка — открой её
и вызови assembly_parts(id, сборка, что нужно); если предложения есть — pick с via=assembly (в parts — сама сборка,
в want — что нужно).
Деталь отдельно не продаётся ни у кого, но есть сборка, в которую она входит (подшипник — в ступице, датчик — в колодках),
— pick found=true с этой сборкой и reason «отдельно не продаётся — только в сборе с …»: клиенту лучше вариант, чем «нет».
Нет детали ни в каталоге, ни у поставщиков, ни в сборе — found=false; reason — ОДНА короткая фраза для клиента, без id
групп и позиций. Фирма клиента тебя не касается — подбирай саму деталь. Не больше 6 вызовов инструментов до pick.
Узел нужный, а детали с подходящим названием в нём нет — прежде чем found=false, открой show_scheme этой группы: названия
каталога — машинный перевод («изолятор пружины» на рисунке может оказаться гофрированным пыльником). Найди деталь по
виду, возьми её pos и название из show_parts, в why напиши, что узнал по схеме.
В pick всегда заполняй why — одной-двумя фразами: где искал, почему взял именно эти детали и что отбросил (похожее
название, не та ось, не тот узел). Это для разбора ошибок, клиент его не видит.{notes}"""
SEPARATE = ("\nКлиент просит ИМЕННО ОТДЕЛЬНО, не в сборе: сборку вместо детали не предлагай — только саму деталь из "
            "каталога или через assembly_parts; нет нигде — found=false и в reason, что отдельно не продаётся.")

SELLER = """Ты — Кирилл, консультант магазина автозапчастей Автодруг92 (Севастополь), пишешь клиенту в чат. Машина: {car}.
Потребность клиента: {need}.
Ниже — данные подбора: по каждой детали номер каталога и предложения поставщиков: бренд, цена, срок в днях, метки
и quality — OE (поставщик этой детали на конвейер, по сути оригинал без логотипа), good (проверенный aftermarket),
eco (эконом); why — почему бренд такого уровня. via=assembly — производитель отдельно не продаёт (note), аналоги есть.
Напиши ответ клиенту:
{variants}
- детали certainty=maybe — отдельным блоком «Проверьте при осмотре» с объяснением зачем;
- found=false — честно, с reason («отдельно не продаётся, только в сборе с …» или «передам менеджеру»);
- оставшиеся вопросы — коротко в конце; в самом конце — «Напишите, какие варианты оформить».
{greet}
Цены, сроки, бренды, номера и уровни — ТОЛЬКО из данных, ничего не придумывай. Номер — только у оригинала.
Про гарантии не пиши. Пиши по-русски, просто, без воды.
Оформление строго так (Telegram, без таблиц и без «###»):
🔧 **Название детали** · 2 шт.
• Оригинал <марка> — 8 080 ₽ · 5 дн.
• ✅ Качество: CTR — 1 470 ₽ · 1 дн. Поставщик автозаводов на конвейер.
• Альтернатива: Masuma — 960 ₽ · 1 дн.
(пустая строка между деталями; разделы — «🔍 Проверьте при осмотре», «❓ Вопросы»; цены с пробелом в тысячах)"""

SELLER_ITEM = """Ты — Кирилл, консультант магазина автозапчастей Автодруг92. Машина: {car}. Потребность клиента: {need}.
Ниже — данные по ОДНОЙ детали: номер каталога и предложения поставщиков (бренд, цена, срок в днях, quality: OE —
поставщик автозаводов, good — проверенный производитель аналогов, eco — эконом; why — описание бренда).
Напиши блок только про эту деталь, строго в формате:
🔧 **Название детали** · N шт.
• Оригинал <марка> — 8 080 ₽ · 5 дн.
• ✅ Качество: CTR — 1 470 ₽ · 1 дн. <why дословно, с заглавной буквы>
• Альтернатива: Masuma — 960 ₽ · 1 дн.
«✅ Качество» — только для quality=OE; good — «Альтернатива», eco — «Эконом». Нет OE — строки «Качество» нет.
N шт. — из catalog[].qty (по каталогу); нет — qty детали; pair=true — после названия «(левый и правый одинаковые)».
Несколько номеров с side «левый»/«правый» — по подзаголовку на сторону («Левый:», «Правый:»), по 1 шт. на сторону.
Первая строка блока — ВСЕГДА «🔧 **Название детали** · N шт.», подзаголовки сторон — только после неё.
{variants}
Если certainty=maybe — одна строка «Зачем проверить: …» сразу после названия. via=assembly или note — одна строка,
что производитель отдельно не продаёт. found=false — одна строка с reason или «передам менеджеру». reason при
found=true (например «отдельно не продаётся — только в сборе со ступицей») — одной строкой после названия.
brand_wanted — фирма, которую просил клиент: если role=«просили» есть в предложениях — первой строкой «• Ваша фирма:
… — цена · срок»; brand_found=false — одной строкой «<фирма> сейчас нет в продаже по этому номеру», дальше варианты.
Цены, сроки, бренды, номера — ТОЛЬКО из данных. После срока — только why дословно; why пустой — после срока
ничего. Своих оценок не добавляй (никаких «лучшее соотношение», «надёжнее», «гарантия посадки», стран производства).
Про гарантии не пиши. К клиенту — на «вы», никогда не «клиент …» в третьем лице. Цены с пробелом в тысячах.
Без приветствий, вступлений и выводов — только блок."""

VARIANTS = {
    "quality": "- клиенту важно КАЧЕСТВО («делаем надолго»): по каждой детали «Оригинал», «Качество» — лучший OE-поставщик "
               "из данных (объясни why), при наличии «Хорошая альтернатива» (good). Эконом (eco) не предлагай, кроме случая, "
               "когда другого нет — тогда честно скажи, что качественного аналога сейчас нет в продаже;",
    "optimal": "- по каждой детали до трёх вариантов: «Оригинал», «Оптимально» (лучшее соотношение качества и цены — "
               "предпочитай OE и good), «Эконом» (дешевле «Оптимально») — с коротким обоснованием каждого;",
    "cheap": "- клиенту важна ЦЕНА: по каждой детали «Эконом» — самый дешёвый разумный вариант (не подозрительно дешёвый), "
             "и «Чуть дороже, но надёжнее» — OE или good, если есть; оригинал — одной строкой для сравнения;",
}

T_SEARCH = {"type": "function", "function": {
    "name": "search_catalog", "description": "Найти группы деталей в каталоге этой машины по словам: id, название, путь.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}
T_UNITS = {"type": "function", "function": {
    "name": "search_units", "description": "Поиск по узлам ПОЛНОГО каталога машины (медленнее): когда в быстрых группах "
    "search_catalog нужной детали нет. Узлы у части марок названы по-английски («FRONT BRAKE», «REAR "
    "SUSPENSION») — ничего не нашлось по-русски, ищи по-английски. Возвращает узлы с id «u…»; открывай их show_parts.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}
T_PARTS = {"type": "function", "function": {
    "name": "show_parts", "description": "Детали группы каталога этой машины: название, позиция на схеме, примечание. "
    "all=true — все детали узлов этой группы, и соседние (цапфа, кулак, кронштейны), которых нет в составе группы.",
    "parameters": {"type": "object", "properties": {"id": {"type": "string"}, "all": {"type": "boolean"}},
                   "required": ["id"]}}}
T_SCHEME = {"type": "function", "function": {
    "name": "show_scheme", "description": "Картинка схемы узла этой группы: номера на рисунке — pos деталей из show_parts. "
    "Названия в каталоге — машинный перевод и бывают неверными; по рисунку видно, что это за деталь на самом деле.",
    "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}}
T_ASSEMBLY = {"type": "function", "function": {
    "name": "assembly_parts",
    "description": "Предложения поставщиков детали, которую производитель отдельно не продаёт, к сборке из каталога",
    "parameters": {"type": "object", "properties": {
        "id": {"type": "string"}, "assembly": {"type": "string", "description": "название сборки из show_parts"},
        "want": {"type": "string", "description": "что нужно: сайлентблок, пыльник, втулка…"}},
        "required": ["id", "assembly", "want"]}}}
T_PICK = {"type": "function", "function": {
    "name": "pick", "description": "Итог по детали",
    "parameters": {"type": "object", "properties": {
        "found": {"type": "boolean"}, "id": {"type": "string"},
        "parts": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "pos": {"type": "string"}}, "required": ["name"]}},
        "via": {"type": "string", "enum": ["catalog", "assembly"]},
        "want": {"type": "string"}, "reason": {"type": "string"},
        "why": {"type": "string", "description": "для разбора, клиент не видит: где искал, почему именно эта деталь, "
                                                 "что отбросил и почему"}}, "required": ["found", "why"]}}}
T_DECODED = {"type": "function", "function": {
    "name": "decoded", "description": "Точный список деталей",
    "parameters": {"type": "object", "properties": {
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "need": {"type": "string", "description": "название детали, как в каталоге"},
            "axis": {"type": "string", "enum": ["front", "rear", ""]},
            "qty": {"type": "integer"}, "certainty": {"type": "string", "enum": ["sure", "maybe"]},
            "words": {"type": "string", "description": "слова клиента"}, "note": {"type": "string"},
            "separate": {"type": "boolean", "description": "клиент хочет именно отдельно, не в сборе"},
            "brand": {"type": "string", "description": "фирма, которую назвал клиент («nagamochi», «Bosch»), — не в need"},
            "why": {"type": "string", "description": "для разбора, клиент не видит: как понял слова клиента — что во что "
                                                     "перевёл и на каком основании"}},
            "required": ["need", "axis", "certainty", "words", "why"]}},
        "questions": {"type": "array", "items": {"type": "string"}},
        "not_parts": {"type": "array", "items": {"type": "string"}},
        "intent": {"type": "string", "enum": ["parts", "order", "more", "number", "scheme", "question"]},
        "order": {"type": "string"}, "brand": {"type": "string"}, "more_of": {"type": "string"},
        "numbers": {"type": "array", "items": {"type": "string"}}}, "required": ["items", "intent"]}}}

T_PHOTO = {"type": "function", "function": {
    "name": "photo", "description": "Что видно на фото клиента",
    "parameters": {"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["sts", "pts", "vin_plate", "car", "part", "other"]},
        "vin": {"type": "string", "description": "VIN, 17 знаков, как напечатан"},
        "plate": {"type": "string", "description": "госномер"},
        "frame": {"type": "string", "description": "номер кузова японских машин (например GX110-1234567)"},
        "numbers": {"type": "array", "items": {"type": "string"}, "description": "номера, выбитые на детали"},
        "part": {"type": "string", "description": "что за деталь, если на фото деталь"}}, "required": ["kind"]}}}

PHOTO_READ = """Клиент магазина автозапчастей прислал фото. Прочитай только то, что нужно для подбора: VIN, госномер или
номер кузова (СТС, ПТС, табличка в проёме двери, под лобовым стеклом), а если это деталь — выбитые на ней номера и что
это за деталь. ФИО, адреса и прочие личные данные не переписывай. Не уверен в знаке — оставь поле пустым.
Ответь вызовом photo."""

T_ORDER = {"type": "function", "function": {
    "name": "order", "description": "Что клиент оформляет — только из предложений прошлого подбора",
    "parameters": {"type": "object", "properties": {
        "picks": {"type": "array", "items": {"type": "object", "properties": {
            "item": {"type": "integer", "description": "номер детали в данных (i)"},
            "brand": {"type": "string"}, "price": {"type": "number"}, "qty": {"type": "integer"}},
            "required": ["item", "brand", "price"]}},
        "unclear": {"type": "string", "description": "что переспросить, если выбор неясен"}}, "required": ["picks"]}}}

ORDER = """Клиент оформляет заказ по прошлому подбору. Машина: {car}. Ниже — подобранные детали (i — номер) с
предложениями (бренд, цена, срок, quality). Определи, что именно он берёт, по его словам: «оригинал по масляному»,
«качество везде», «что советуешь» (на твоё усмотрение — по каждой детали «Качество» (OE), если нет — «Альтернатива»
(good), если нет — оригинал). Детали «под вопросом» (certainty=maybe) не бери, если клиент их явно не назвал.
qty — сколько штук (по умолчанию qty детали). Только бренды и цены из данных. Неясно — picks пусто, в unclear — вопрос.
Ответь вызовом order."""

# Главные слова, которые поставщики пишут по-разному об одной детали: «Стойка газомасляная задняя» — амортизатор
_SAME_HEAD = [{T.stem(w) for w in g} for g in (("амортизатор", "стойка"), ("сайлентблок", "втулка", "с/блок"),
                                                ("наконечник", "тяга"), ("опора", "подушка"))]


def _heads_match(a: str, b: str) -> bool:
    return T.same(a, b) or any(any(T.same(a, x) for x in g) and any(T.same(b, x) for x in g) for g in _SAME_HEAD)


def check(part: str, offers: list[dict], car_brand: str) -> tuple[list[dict], list[str]]:
    """Проверяющий (код, без модели). Аналог в разы дешевле оригинала и с другой главной деталью в описании — не тот
    товар (26.10, Lexus: «опора» за 310 ₽ при оригинале 4 280 ₽ — подшипник опоры, «рычаг» за 430 ₽ — колодки).
    Оригинал — под маркой машины: ABCP отдаёт номер Toyota и под DAIHATSU, клиенту это «Toyota (оригинал)»."""
    maker = nice_brand(oem_brand_for(car_brand))
    orig = next((o for o in offers if o.get("role") == "оригинал"), None)
    head = _desc_head(part) or (T.head(T.stems(T.expand(part))) or "")
    kept, dropped = [], []
    for o in offers:
        if o.get("role") == "оригинал":
            o = dict(o, brand=f"{maker} (оригинал)")
        elif orig and o["price"] < 0.35 * orig["price"]:
            h = _desc_head(o.get("description", ""))
            if h and head and not _heads_match(h, head):
                dropped.append(f"{o['brand']} {o['price']:.0f} ₽ «{o.get('description', '')[:40]}»")
                continue
        kept.append(o)
    return kept, dropped


class Sessions:
    """Память разговора: машина и последние реплики — файл на собеседника в <state>/assistant/sessions."""

    def __init__(self, folder: Path | None):
        self.folder = folder

    def _path(self, sid: str) -> Path | None:
        return self.folder / (re.sub(r"[^A-Za-z0-9_-]", "_", sid)[:80] + ".json") if self.folder else None

    def load(self, sid: str) -> dict:
        p = self._path(sid)
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p and p.exists() else {}
        except (OSError, ValueError):
            return {}

    def save(self, sid: str, data: dict):
        p = self._path(sid)
        if not p:
            return
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


class Assistant:
    def __init__(self, engine, gateway: Gateway, model: str, folder: Path | None, journal=None, warranty: dict | None = None,
                 decoder_model: str = ""):
        self.eng, self.gw, self.model, self.journal = engine, gateway, model, journal
        self.decoder_model = decoder_model or model   # расшифровка — знание устройства машины, можно модель сильнее
        self.cat = engine.catalog
        self.sessions = Sessions(folder / "sessions" if folder else None)
        self.images = folder.parent / "podbor" / "schemes" if folder else None   # тот же кэш, что у страницы подбора
        self.warranty = warranty or {}
        # Фото деталей из прайса СТМ поставщика (выжимка без цен — STATE_DIR/stm/parts.json, не в git): нет файла — без фото
        self.stm = STM.Price(folder.parent / "stm" / "parts.json" if folder else None)
        self.pace = asyncio.Semaphore(6)   # параллельных подборщиков: Laximo 600/мин на весь сервис
        self.locks: dict[str, asyncio.Lock] = {}   # один разговор — по очереди: двойное «Подобрать» не подбирает дважды
        self.notify = None   # async (текст) — карточка в группу менеджеров; ставит Telegram-бот

    # ---------- инструменты подборщика ----------

    async def search(self, tree, query: str, v=None) -> list[dict]:
        """Кандидаты из дерева групп машины: правила робота (основы, синонимы, опечатки) + векторы по смыслу.
        Быстрых групп у машины нет (SsangYong Kyron: E_NOTSUPPORTED) — сразу узлы полного каталога."""
        if tree is None:
            return await self.search_units(v, query) if v is not None else []
        st = T.stems(query, self.eng.stop)
        out: dict[str, dict] = {}
        for g, _ in tree.rank(st, T.Side())[:8] if st else []:
            out.setdefault(f"g{g.id}", {"id": f"g{g.id}", "name": g.name, "path": g.path[-80:]})
        emb = EM.get()
        if emb is not None:
            vec = await emb.embed([query])
            names = {g.name.lower(): g for g in tree.groups.values()}
            for name, _ in emb.top(vec[0] if vec else None, [g.name for g in tree.groups.values()], 8):
                g = names.get(name.lower())
                if g:
                    out.setdefault(f"g{g.id}", {"id": f"g{g.id}", "name": g.name, "path": g.path[-80:]})
        return list(out.values())[:14]

    async def search_units(self, v, query: str) -> list[dict]:
        """Узлы полного каталога машины (как у робота: Catalog.unit_tree — один раз обходит каталог, дальше с диска):
        когда в быстрых группах нужной детали нет. id — «u<номер>»."""
        ut = await self.cat.unit_tree(v)
        st = T.stems(query, self.eng.stop)
        if ut is None or not st:
            return [{"info": "полного каталога по узлам у этой машины нет"}]
        return [{"id": f"u{-g.id}", "name": g.name, "path": g.path[-80:]} for g, _ in ut.rank(st, T.Side())[:10]] \
            or [{"info": "в узлах по этим словам ничего нет — попробуй другое название"}]

    async def _details(self, v, tree, gid: str, full: bool = False) -> list | None:
        """Детали группы («g123»; full — все детали её узлов) или узла полного каталога («u45»); None — такого id нет."""
        if gid[1:].isdigit() and gid[:1] == "g" and tree is not None and int(gid[1:]) in tree.groups:
            return await self.cat.details(v, int(gid[1:]), full)
        if gid[1:].isdigit() and gid[:1] == "u":
            ut = await self.cat.unit_tree(v)
            if ut is not None and -int(gid[1:]) in ut.groups:
                return await self.cat.details(v, -int(gid[1:]), False)
        return None

    async def parts(self, v, tree, gid: str, full: bool = False) -> list[dict]:
        ds = await self._details(v, tree, gid, full)
        if ds is None:
            return [{"error": "нет такой группы, возьми id из search_catalog или search_units"}]
        seen, out = set(), []
        for d in ds:
            if (d.name, d.code_on_image) in seen:
                continue
            seen.add((d.name, d.code_on_image))
            out.append({"name": d.name[:70], "pos": d.code_on_image, "note": (d.note or d.specs or "")[:60],
                        "unit": d.unit[:50]})
        return out[:40 if full else 30] or [{"info": "в группе нет деталей"}]

    async def assembly_rows(self, v, gid: str, assembly: str, want: str, tree=None) -> list[dict]:
        """Как робот (_bushings_by_host): кроссы номера сборки у поставщиков — только строки нужной детали."""
        tree = tree if tree is not None else await self._tree(v)
        hosts: list = []
        for full in (False, True):   # цапфа у Toyota — не в составе группы, а в её узле
            ds = await self._details(v, tree, gid, full) or []
            hosts = [d for d in ds if d.name[:70] == assembly] or [d for d in ds if assembly[:20].lower() in d.name.lower()]
            if hosts:
                break
        wst = T.stems(T.expand(want))
        if not hosts or not wst:
            return []
        head = _desc_head(want) or (T.head(wst) or "")
        # Уточнение («цапфы», «плавающий», «переднего рычага») — в описании поставщика обязательно (26.10, Lexus: к рычагу
        # задней подвески поставщики дают сайлентблоки поперечной тяги, а клиент просил плавающие — они в цапфе)
        quals = [w for w in wst if not T.same(w, head) and not any(T.same(w, g) for g in _GENERIC)]
        out, seen = [], set()
        for d in hosts[:2]:
            _, rows = await self.eng.offers(d.oem, v.brand)
            for r in rows or []:
                desc = str(r.get("description") or "")
                h = _desc_head(desc)
                if not h or not any(T.same(h, w) for w in wst) or (r.get("brand"), r.get("number")) in seen:
                    continue
                if quals and not any(T.same(q, x) for q in quals for x in T.stems(T.expand(desc))):
                    continue
                seen.add((r.get("brand"), r.get("number")))
                out.append(r)
        return out

    async def _tree(self, v):
        try:
            return await self.cat.tree(v)
        except Exception:  # noqa: BLE001 — нет быстрых групп
            return None

    # ---------- агенты ----------

    async def decode_parts(self, car: str, history: list[dict], text: str, used: dict, brand: str = "") -> dict:
        """Длинная заявка — по строкам (27.10, руководитель: на 15 позициях расшифровщик то брал передние втулки
        стабилизатора, то терял). Цельный разбор — для намерения и вопросов, детали — из разборов каждой строки отдельно
        (всё сообщение у каждой — контекст: «Передок:» задаёт ось следующим строкам); всё параллельно."""
        chunks = [c for c in T.split_chunks(text) if re.search(r"[а-яёa-z]{3}", c, re.I)]
        if len(chunks) < SPLIT_FROM:
            return await self.decode(car, history, text, used, brand)
        whole, *parts = await asyncio.gather(self.decode(car, history, text, used, brand),
                                             *(self.decode(car, history, text, used, brand, focus=c) for c in chunks))
        if (whole.get("intent") or "parts") != "parts":
            return whole
        items, seen, questions = [], set(), []
        for p in parts:
            for it in p.get("items") or []:
                key = (re.sub(r"\s+", " ", str(it.get("need", "")).lower().strip()), it.get("axis") or "")
                if key[0] and key not in seen:
                    seen.add(key)
                    items.append(it)
            questions += [q for q in p.get("questions") or [] if q not in questions]
        if not items:
            return whole
        return dict(whole, items=items, questions=questions or whole.get("questions") or [], split=len(chunks))

    async def decode(self, car: str, history: list[dict], text: str, used: dict, brand: str = "", focus: str = "") -> dict:
        said = focus or " ".join([h["content"] for h in history if h.get("role") == "user"
                                  and isinstance(h.get("content"), str)] + [text])
        system = DECODER.format(car=car, notes=_notes("decoder", said, brand)) + (FOCUS.format(part=focus) if focus else "")
        msgs = [{"role": "system", "content": system}] + history + [{"role": "user", "content": text}]
        for _ in range(2):   # tool_choice у Claude через шлюз не поддерживается — просим словами
            m, u = await self.gw.chat(self.decoder_model, msgs, [T_DECODED])
            _add(used, u)
            c = (m.get("tool_calls") or [None])[0]
            if c:
                try:
                    return json.loads(c["function"].get("arguments") or "{}")
                except ValueError:
                    return {"items": []}
            msgs += [{"role": "assistant", "content": m.get("content") or ""},
                     {"role": "user", "content": "Ответь вызовом decoded."}]
        return {"items": []}

    async def select(self, v, tree, item: dict, used: dict) -> dict:
        async with self.pace:
            # Кандидаты и детали лучшей группы — сразу в первом сообщении (26.10: подборщик ходил по каталогу по 5–7 шагов,
            # подбор большого запроса шёл минуту): для частых деталей хватает одного вызова модели
            first = [c for c in await self.search(tree, item["need"], v) if c.get("id")]
            top = await self._safe(self.parts(v, tree, first[0]["id"]), []) if first else []
            msgs = [{"role": "system", "content": SELECTOR.format(
                        car=v.summary(), need=item["need"], axis=item.get("axis") or "любая", words=item.get("words", ""),
                        separate=SEPARATE if item.get("separate") else "",
                        notes=_notes("selector", f"{item['need']} {item.get('words', '')}", v.brand))},
                    {"role": "user", "content": "Кандидаты из каталога: " + json.dumps(first, ensure_ascii=False)
                     + (f"\nДетали группы {first[0]['id']}: " + json.dumps(top, ensure_ascii=False) if first else "")
                     + "\nПодбери."}]
            looked = 0
            for _ in range(SELECT_STEPS):
                m, u = await self.gw.chat(self.model, msgs, [T_SEARCH, T_UNITS, T_PARTS, T_SCHEME, T_ASSEMBLY, T_PICK])
                _add(used, u)
                calls = m.get("tool_calls") or []
                msgs.append({"role": "assistant", "content": m.get("content") or "", **({"tool_calls": calls} if calls else {})})
                if not calls:
                    msgs.append({"role": "user", "content": "Вызови pick."})
                    continue
                pictures: list[dict] = []
                for c in calls:
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    fn = c["function"]["name"]
                    if fn == "pick":
                        return args
                    try:
                        if fn == "show_scheme":
                            # Картинку модель видит только в реплике пользователя: в ответе инструмента — что за узлы
                            got = await self.scheme_pictures(v, tree, str(args.get("id", ""))) if looked < MAX_SCHEMES else []
                            looked += len(got)
                            pictures += got
                            res = [{"unit": unit} for unit, _ in got] or \
                                [{"info": "схемы нет" if looked < MAX_SCHEMES else "схем на эту деталь уже хватит"}]
                        else:
                            res = await self._tool(v, tree, fn, args)
                    except Exception as e:  # noqa: BLE001 — Laximo «группа недоступна для поиска» (26.10, Lexus)
                        log.info("инструмент %s: %s", fn, type(e).__name__)
                        res = [{"error": "эта группа сейчас недоступна, возьми другую из search_catalog"}]
                    msgs.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(res, ensure_ascii=False)})
                if pictures:
                    msgs.append({"role": "user", "content": [
                        {"type": "text", "text": "Схемы узлов (номера на рисунке — pos деталей): "
                                                 + "; ".join(unit for unit, _ in pictures)}]
                        + [{"type": "image_url", "image_url": {"url": url}} for _, url in pictures]})
            return {"found": False, "reason": "шаги кончились"}

    async def scheme_pictures(self, v, tree, gid: str) -> list[tuple[str, str]]:
        """Схемы узлов группы для подборщика: [(узел, data-URL JPEG)], не больше двух узлов, уменьшенные (26.10, Lexus
        RX330: пыльник стойки в каталоге назван «изолятор пружины» — по названию Кирилл его отбросил, на рисунке он
        очевиден)."""
        import base64
        from .scheme_img import render
        ds = await self._details(v, tree, gid, True) or []
        out, seen = [], set()
        for d in ds:
            if not d.image or d.unit in seen:
                continue
            seen.add(d.unit)
            img = await self._image(image_url(d.image))
            if not img:
                continue
            jpg = await asyncio.to_thread(render, img, [], SCHEME_SIDE)
            out.append((d.unit[:60], "data:image/jpeg;base64," + base64.b64encode(jpg).decode()))
            if len(out) >= 2:
                break
        return out

    async def _tool(self, v, tree, fn: str, args: dict) -> Any:
        if fn == "search_catalog":
            return await self.search(tree, str(args.get("query", "")), v)
        if fn == "search_units":
            return await self.search_units(v, str(args.get("query", "")))
        if fn == "assembly_parts":
            rows = await self.assembly_rows(v, str(args.get("id", "")), str(args.get("assembly", "")),
                                            str(args.get("want", "")))
            return [{"brand": r.get("brand"), "description": str(r.get("description") or "")[:70],
                     "price": r.get("price")} for r in rows[:8]] or [{"info": "у поставщиков такой детали нет"}]
        return await self.parts(v, tree, str(args.get("id", "")), bool(args.get("all")))

    async def _safe(self, coro, fallback):
        """Сбой одной детали (каталог, поставщики, модель) не роняет весь ответ: по ней — «передам менеджеру»."""
        try:
            return await coro
        except OverBudget:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("деталь не подобрана: %s: %s", type(e).__name__, str(e)[:150])
            return fallback

    def _offer(self, o: dict, role: str = "") -> dict:
        t, why = ("OE", "оригинал") if role == "оригинал" else Q.tier(o["brand"])
        return {"brand": o["brand"], "price": o["price"], "days": o["days"], "tags": o.get("tags", []),
                "description": o.get("description", ""), "role": role or o.get("role", ""), "offers": o.get("offers", 1),
                "quality": t, "why": why}

    def quality(self, rows: list[dict], car_brand: str, name: str, have: set[str]) -> list[dict]:
        """Лучшие по качеству фирмы из ВСЕХ предложений по номеру (curate оставляет пять ходовых — быстрых и дешёвых,
        качественные фирмы до продавца не доходили): OE, потом good. Не оригинал, не чужой товар по описанию;
        по бренду — самое быстрое предложение."""
        own = {Q.key(x) for x in original_keys(car_brand)}
        best: dict[str, dict] = {}
        for r in rows or []:
            k = Q.key(r.get("brand"))
            t, _ = Q.tier(r.get("brand"))
            if t == "eco" or k in own or k in have:
                continue
            if name and not_the_part(str(r.get("description") or ""), name):
                continue
            try:
                price = float(r.get("price") or 0)
            except (TypeError, ValueError):
                continue
            cur = best.get(k)
            if price > 0 and (cur is None or (days(r.get("deliveryPeriod")), price) <
                              (days(cur.get("deliveryPeriod")), float(cur.get("price") or 0))):
                best[k] = r
        out = [self._offer(_offer(r)) for r in best.values()]
        out.sort(key=lambda o: (o["quality"] != "OE", o["days"], o["price"]))
        return out[:3]

    async def priced(self, v, item: dict, pick: dict) -> list[dict]:
        """Выбранные детали → номера каталога → предложения ABCP → проверяющий."""
        gid = str(pick.get("id") or "")
        if not pick.get("found") or not gid[1:].isdigit():
            return []
        tree = await self._tree(v)
        if pick.get("via") == "assembly":
            host = (pick.get("parts") or [{}])[0].get("name", "")
            want = pick.get("want") or item["need"]
            rows = await self.assembly_rows(v, gid, host, want, tree)
            if not rows:
                return []
            cur = curate(rows, "", v.brand, set(), name=want)
            offers = [self._offer(o) for o in cur.get("analogs", [])[:6]]
            offers += self.quality(rows, v.brand, "", {Q.key(o["brand"]) for o in offers})
            return [{"part": f"{want} (к сборке «{host}»)", "oem": "", "pos": "", "via": "assembly",
                     "note": f"{nice_brand(oem_brand_for(v.brand))} отдельно не продаёт — только {host}",
                     "offers": offers, "dropped": []}]
        ds = await self._details(v, tree, gid) or []
        wanted = pick.get("parts") or []
        if wanted and not any(_match(d, w) for d in ds for w in wanted):
            ds = await self._details(v, tree, gid, True) or []   # выбрано из всех деталей узла (show_parts all=true)
        out: list[dict] = []
        for p in wanted:
            # Не по точному названию (26.10, стенд: модель чуть меняет название — «торм.», обрезка — и выбранные колодки
            # Ford теряли цены): позиция на схеме + название по смыслу
            cands = [d for d in ds if _match(d, p)]
            d = max(cands, key=lambda d: _name_score(d.name, p.get("name", "")), default=None)
            if not d or any(o["oem"] == d.oem for o in out):
                continue
            _, rows = await self.eng.offers(d.oem, v.brand)
            cur = curate(rows or [], d.oem, v.brand, set(), name=d.name)
            offers = ([self._offer(cur["original"], "оригинал")] if cur.get("original") else []) + \
                [self._offer(o) for o in cur.get("analogs", [])]
            offers = [_why_for(o, d.name) for o in offers]
            kept, dropped = check(d.name, offers[:7], v.brand)
            kept = kept[:6]
            want = Q.key(item.get("brand") or "")
            if want:
                # «Колодки к ним nagamochi» (26.10, стенд): фирма — фильтр предложений, а не часть детали
                mine = [r for r in rows or [] if Q.key(r.get("brand")).startswith(want[:6])]
                best = min(mine, key=lambda r: (days(r.get("deliveryPeriod")), float(r.get("price") or 0)), default=None)
                kept = [o for o in kept if not Q.key(o["brand"]).startswith(want[:6])]
                if best is not None:
                    kept = [dict(self._offer(_offer(best)), role="просили")] + kept
            extra, more = check(d.name, [_why_for(o, d.name) for o in
                                         self.quality(rows, v.brand, d.name, {Q.key(o["brand"]) for o in kept})], v.brand)
            if dropped or more:
                log.info("проверяющий убрал: %s", dropped + more)
            # Сколько и какая сторона — по каталогу, как у робота (26.10: «Тяга рулевая · 1 шт.», хотя их две):
            # один номер на левую и правую — одна деталь на обе стороны, на машину две; «amount» каталога — штук на узел
            lrs = {T.side(x.name).lr for x in ds if x.oem == d.oem} - {""}
            pair = {"left", "right"} <= lrs
            k = re.match(r"\d+", str(d.amount or ""))
            amount = int(k.group(0)) if k else 0
            side = "" if pair else {"left": "левый", "right": "правый"}.get(T.side(d.name).lr, "")
            out.append({"part": d.name, "oem": d.oem, "pos": d.code_on_image, "offers": kept + extra,
                        "qty": (2 * (amount or 1)) if pair else (amount or None), "pair": pair, "side": side,
                        "brand_wanted": item.get("brand") or "", "brand_found": any(o.get("role") == "просили" for o in kept),
                        "numbers": (cur.get("numbers") or [])[:60],   # кроссы: «а этот номер подойдёт?»
                        "dropped": dropped + more,
                        "scheme": {"catalog": v.catalog, "unit_id": d.unit_id, "ssd": d.unit_ssd,
                                   "image": image_url(d.image), "code": d.code_on_image, "unit": d.unit} if d.image else None})
        return out

    async def sell_item(self, car: str, item: dict, used: dict, need: str, goal: str) -> str:
        m, u = await self.gw.chat(self.model, [
            {"role": "system", "content": SELLER_ITEM.format(car=car, need=need, variants=VARIANTS[goal])},
            {"role": "user", "content": json.dumps(_no_schemes(item), ensure_ascii=False)}], max_out=4000)
        _add(used, u)
        return (m.get("content") or "").strip()

    async def _block(self, car: str, it: dict, used: dict, prefs: dict) -> str:
        """Блок ответа по одной детали; не нашли — без модели, честная строка с причиной."""
        goal, need = _goal_need(prefs)
        if not it.get("found"):
            why = re.sub(r"\s*\((?:группа\s+)?g-?\d+[^)]*\)|\bg-?\d+\b", "", it.get("reason") or "").strip()
            why = re.split(r"(?<=[.!?])\s", why)[0][:200]   # одна фраза: подборщик бывает многословен
            return f"🔧 **{it['need']}** — " + (f"{why[0].lower()}{why[1:]}" if why else "передам менеджеру, он подберёт.")
        return await self._safe(self.sell_item(car, it, used, need, goal), "") or \
            f"🔧 **{it['need']}** — передам менеджеру."

    async def sell_items(self, car: str, data: dict, used: dict, prefs: dict, greet: bool,
                         blocks: list[str] | None = None) -> str:
        """Ответ по деталям: блоки пишутся параллельно (26.10: один длинный ответ на 13 позиций модель печатала
        больше минуты), рамку — приветствие, «Проверьте при осмотре», вопросы — собирает код: всегда одинаково.
        blocks — уже написанные (ответ по частям)."""
        goal, _ = _goal_need(prefs)
        items = data["items"]
        if blocks is None:
            blocks = await asyncio.gather(*(self._block(car, it, used, prefs) for it in items))
        sure = [b or f"🔧 **{it['need']}** — передам менеджеру." for it, b in zip(items, blocks)
                if it.get("certainty") != "maybe"]
        maybe = [b or f"🔧 **{it['need']}** — передам менеджеру." for it, b in zip(items, blocks)
                 if it.get("certainty") == "maybe"]
        car = re.sub(r"\b(\w+) \1\b", r"\1", car, flags=re.I)   # «LEXUS LEXUS RX330/350» — Laximo повторяет марку
        head = (f"Здравствуйте! Подобрал по вашему {car}" if greet else f"Подобрал по {car}") + \
            f" — {GOALS[goal].lower()}" + (", срочно." if prefs.get("when") == "urgent" else ".")
        parts = [head] + sure
        if maybe:
            parts += ["🔍 **Проверьте при осмотре**"] + maybe
        qs = [q for q in data.get("questions") or [] if q.strip()]
        if qs:
            parts += ["❓ **Вопросы**\n" + "\n".join(f"{n}. {q}" for n, q in enumerate(qs, 1))]
        parts.append("Напишите, какие варианты оформить.")
        data["footer"] = "\n\n".join(parts[parts.index(maybe[-1]) + 1 if maybe else len(sure) + 1:])
        return "\n\n".join(parts)

    async def sell(self, car: str, data: dict, used: dict, prefs: dict | None = None, greet: bool = False) -> str:
        prefs = prefs or {}
        goal = prefs.get("goal") if prefs.get("goal") in VARIANTS else "optimal"
        need = GOALS[goal] + ("; срочно — в первую очередь то, что привезём за 1–2 дня" if prefs.get("when") == "urgent"
                              else "; можно подождать ради цены и качества" if prefs.get("when") == "wait" else "")
        hello = ("Это первый ответ клиенту — коротко поздоровайся (одной фразой)." if greet
                 else "Разговор уже идёт — не здоровайся, сразу к делу.")
        m, u = await self.gw.chat(self.model, [{"role": "system", "content": SELLER.format(car=car, need=need,
                                                                                          variants=VARIANTS[goal],
                                                                                          greet=hello)},
                                               {"role": "user", "content": json.dumps(data, ensure_ascii=False)}],
                                  max_out=20000)
        _add(used, u)
        return (m.get("content") or "").strip()

    # ---------- схемы ----------

    async def scheme(self, sid: str, i: int) -> tuple[bytes, str] | None:
        """Схема узла i-й найденной детали прошлого подбора с отмеченной позицией: (JPEG, подпись) или None."""
        sch = (self.sessions.load(sid).get("schemes") or [])
        if not 0 <= i < len(sch):
            return None
        return await self.render_scheme(sch[i])

    async def render_scheme(self, x: dict) -> tuple[bytes, str] | None:
        """Схема узла с отмеченной позицией: (JPEG, подпись) или None."""
        img = await self._image(x["image"])
        if not img:
            return None
        boxes: list = []
        try:
            m = await self.eng.src.laximo("listImageMapByUnit", {"catalog": x["catalog"], "ssd": x["ssd"],
                                                                "unitId": x["unit_id"]})
            boxes = [b for b in (m if isinstance(m, list) else []) if str(b.get("code")) == str(x["code"])]
        except Exception as e:  # noqa: BLE001 — без координат схема тоже полезна: номер позиции в подписи
            log.info("image map: %s", type(e).__name__)
        from .scheme_img import render
        try:
            jpg = await asyncio.to_thread(render, img, boxes)
        except Exception as e:  # noqa: BLE001 — картинка не та или битая
            log.info("render: %s", type(e).__name__)
            return None
        cap = f"{x['label']} — позиция {x['code']}" + (" (отмечена)" if boxes else "") + \
            (f"\nУзел: {x['unit']}" if x.get("unit") else "")
        return jpg, cap[:1000]

    async def _image(self, url: str) -> bytes | None:
        """Картинка схемы Laximo — с диска (общий кэш с /v1/podbor/scheme) или из каталога."""
        f = self.images / (hashlib.sha1(url.encode()).hexdigest() + ".img") if self.images else None
        if f and f.exists():
            return f.read_bytes()
        try:
            import httpx
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as h:
                r = await h.get(url)
        except Exception:  # noqa: BLE001
            return None
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
            return None
        if f:
            try:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(r.content)
            except OSError:
                pass
        return r.content

    # ---------- разговор ----------

    async def reply(self, sid: str, text: str, action: str = "", on_part=None) -> dict:
        """Ответ собеседнику sid («tg-123»): {"text", "status", "buttons", "used", "data", "seconds"}.
        action — нажатая кнопка: «goal:quality», «when:urgent», «go» (подобрать с тем, что уже ответил).
        Реплики одного собеседника — по очереди (26.10: двойное «Подобрать» подбирало дважды)."""
        lock = self.locks.setdefault(sid, asyncio.Lock())
        async with lock:
            # Куда отдавать готовые детали — в контексте ЭТОЙ реплики: у разных собеседников свои задачи, и общее поле
            # отдало бы детали одного клиента другому
            token = _ON_PART.set(on_part)
            try:
                return await self._reply(sid, text, action)
            finally:
                _ON_PART.reset(token)

    async def _reply(self, sid: str, text: str, action: str) -> dict:
        t0 = time.time()
        s = self.sessions.load(sid)
        if action:
            return await self._action(sid, s, action, t0)
        req = T.parse(text)
        if req.ident or req.plate:
            try:
                found = await self.cat.vehicles(req.ident, req.plate)
            except Exception as e:  # noqa: BLE001 — каталог недоступен: честно, без падения
                log.info("vehicles error: %s", type(e).__name__)
                return self._done(sid, s, text, FAILED, "catalog_error", t0)
            if not found:
                return self._done(sid, s, text, NO_CAR, "vehicle_not_found", t0)
            v = found[0]
            s.update(ident=req.ident, plate=req.plate if not req.ident else "", car=v.summary(), history=[])
            s.pop("asking", None)
            rest = re.sub(re.escape(req.ident or req.plate), " ", text, flags=re.I).strip(" ,.:;-\n")
            if not _asks(rest) and s.get("pending"):
                rest = s.pop("pending")   # сначала просьба, потом VIN — отвечаем на просьбу
            if not _asks(rest):
                return self._done(sid, s, text, f"Машина: {v.summary()}. Что подобрать?", "car", t0)
            text = rest
        if not s.get("ident") and not s.get("plate"):
            s["pending"] = (s.get("pending", "") + "\n" + text).strip()[-1500:]
            return self._done(sid, s, text, ASK_VIN, "no_vin", t0)
        if s.get("schemes") and SCHEME_ASK.search(text) and len(T.words(text)) <= 8:
            # «Покажи, где стоит», «скинь схему» — схема прошлого подбора, а не новый подбор
            sch = s["schemes"]
            hit = [i for i, x in enumerate(sch) if any(T.same(w, y) for w in T.stems(text) for y in T.stems(x["label"]))]
            if len(hit) == 1 or len(sch) == 1:
                res = self._done(sid, s, text, "Вот схема — деталь отмечена красным.", "scheme", t0)
                res["photos"] = hit[:1] or [0]
                return res
            return self._done(sid, s, text, "Какую схему показать?", "scheme", t0, buttons=_scheme_buttons(sch))
        asking = s.pop("asking", None)
        if asking:
            # Ответ текстом на наши вопросы — подбираем по просьбе вместе с ответами
            prefs = dict(asking.get("prefs") or {})
            prefs.update(_prefs_from_text(text))
            return await self._podbor(sid, s, asking["text"] + "\nОтветы клиента на вопросы: " + text, prefs, t0,
                                      shown=text)
        return await self._podbor(sid, s, text, {}, t0, ask=True)

    async def _action(self, sid: str, s: dict, action: str, t0: float) -> dict:
        if action.startswith("sch:") and action[4:].isdigit():
            ok = int(action[4:]) < len(s.get("schemes") or [])
            return {"text": "", "status": "scheme" if ok else "stale", "photos": [int(action[4:])] if ok else [],
                    "ack": "Схема…" if ok else "", "buttons": [], "used": {}, "data": {}, "seconds": 0.0}
        asking = s.get("asking")
        if not asking:
            return {"text": "", "status": "stale", "buttons": [], "used": {}, "data": {}, "seconds": 0.0}
        kind, _, val = action.partition(":")
        if kind == "goal" and val in GOALS or kind == "when" and val in WHEN:
            asking.setdefault("prefs", {})[kind] = val
            self.sessions.save(sid, s)
            if not ({"goal", "when"} <= set(asking["prefs"])) or asking.get("questions"):
                label = GOALS.get(val) or WHEN.get(val)
                return {"text": "", "status": "pref", "ack": f"Принято: {label}", "buttons": [], "used": {},
                        "data": {}, "seconds": 0.0}
        elif action != "go":
            return {"text": "", "status": "stale", "buttons": [], "used": {}, "data": {}, "seconds": 0.0}
        s.pop("asking", None)
        return await self._podbor(sid, s, asking["text"], asking.get("prefs") or {}, t0, shown="(подобрать)")

    async def _podbor(self, sid: str, s: dict, text: str, prefs: dict, t0: float, ask: bool = False,
                      shown: str | None = None) -> dict:
        try:
            v = (await self.cat.vehicles(s.get("ident", ""), s.get("plate", "")))[0]
            try:
                tree = await self.cat.tree(v)
            except Exception as e:  # noqa: BLE001 — SsangYong Kyron (26.10): «E_NOTSUPPORTED:ListQuickGroups»
                log.info("tree: %s", str(e)[:100])
                tree = None
            used = {"in": 0, "out": 0, "calls": 0}
            dec = await self.decode_parts(v.summary(), s.get("history", []), mask(text), used, brand=v.brand)
            # Сначала — чего клиент хочет (26.10, испытание: «оформляй», «а какие есть», «зепро», «схему дай» каждый
            # раз запускали новый подбор)
            intent = dec.get("intent") or "parts"
            if intent == "order" and s.get("last"):
                return await self._order(sid, s, v, text, used, t0, shown)
            if intent == "more" and s.get("last"):
                return await self._more(sid, s, v, dec, text, used, t0, shown)
            if intent == "number" and dec.get("numbers"):
                return await self._numbers(sid, s, v, dec["numbers"], used, t0, shown or text)
            if intent == "scheme":
                sch = s.get("schemes") or []
                return self._done(sid, s, shown or text, "Какую схему показать?" if sch else
                                  "Схему пришлю, когда подберу деталь — напишите, какая нужна.", "scheme", t0, used,
                                  buttons=_scheme_buttons(sch))
            items = (dec.get("items") or [])[:MAX_ITEMS]
            if items and tree is None and await self._safe(self.cat.unit_tree(v), None) is None:
                # Ни быстрых групп, ни полного каталога по узлам — честно менеджеру
                return self._done(sid, s, shown or text, NO_QUICK, "no_quick_groups", t0, used)
            recent = time.time() - float(s.get("last_ok") or 0) < CONVERSATION
            if ask and items and recent:
                # Тот же разговор (26.10: «Диски тормозные передние» в ответ на наш вопрос снова получили «надолго или
                # подешевле?»): потребность уже выяснили — не переспрашиваем, только уточнения по делу, если они есть
                prefs = dict(s.get("last_prefs") or {})
                ask = bool([q for q in dec.get("questions") or [] if q.strip()])
            if ask and items:
                # До подбора — выявить потребность: «надолго или подешевле», «срочно или нет» и уточнения расшифровщика
                qs = [q for q in dec.get("questions") or [] if q.strip()][:MAX_QUESTIONS - (0 if recent else 2)]
                s["asking"] = {"text": text, "questions": qs, "prefs": prefs if recent else {}}
                return self._done(sid, s, shown or text, _questions(items, qs, need_prefs=not recent), "ask", t0, used,
                                  buttons=BUTTONS if not recent else BUTTONS[-1:])
            answer, data = await self._pipeline(v, tree, dec, items, mask(text), prefs, used,
                                                greet=not s.get("last_ok"), on_part=self._parts_cb())
            if answer:
                s["last_ok"], s["last_prefs"] = time.time(), prefs
                s["schemes"] = _schemes(data)
                s["last"] = _last(data)
        except OverBudget:
            return self._done(sid, s, shown or text, FAILED, "over_budget", t0)
        except Exception as e:  # noqa: BLE001 — модель/каталог сбоят: менеджеру, а не тишина
            log.warning("assistant failed: %s: %s", type(e).__name__, str(e)[:200])
            return self._done(sid, s, shown or text, FAILED, "error", t0)
        streamed = bool(data and data.get("streamed"))
        res = self._done(sid, s, shown or text, answer or FAILED, "ok" if answer else "error", t0, used, data,
                         buttons=_scheme_buttons(s.get("schemes") or []) if answer and not streamed else None)
        if streamed:
            res["streamed"], res["footer"] = True, data.get("footer", "")   # детали со схемами уже отправлены
        elif answer and 0 < len(s.get("schemes") or []) <= 2:
            res["photos"] = list(range(len(s["schemes"])))   # 1–2 детали — схемы сразу, без кнопок
            res["buttons"] = []
        return res

    @staticmethod
    def _parts_cb():
        return _ON_PART.get()

    # ---------- после подбора: заказ, ещё варианты, номер клиента ----------

    async def _order(self, sid: str, s: dict, v, text: str, used: dict, t0: float, shown: str | None) -> dict:
        """«Оформляй», «беру оригинал по масляному», «что советуешь» — заказ из прошлого подбора, без нового подбора."""
        last = s["last"]
        m, u = await self.gw.chat(self.model, [{"role": "system", "content": ORDER.format(car=v.summary())},
                                               {"role": "user", "content": json.dumps(
                                                   {"items": last["items"], "client": mask(text)}, ensure_ascii=False)}],
                                  [T_ORDER])
        _add(used, u)
        args: dict = {}
        c = (m.get("tool_calls") or [None])[0]
        if c:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except ValueError:
                args = {}
        picks = []
        for p in args.get("picks") or []:
            i = p.get("item")
            if not isinstance(i, int) or not 0 <= i < len(last["items"]):
                continue
            it = last["items"][i]
            # Только то, что было в подборе: бренд из данных (модель номера и цены не придумывает)
            o = next((o for c in it["catalog"] for o in c["offers"] if Q.key(o["brand"]).startswith(Q.key(p.get("brand"))[:6])
                      and Q.key(p.get("brand"))), None)
            if o:
                qty = int(p.get("qty") or it.get("qty") or 1)
                picks.append({"need": it["need"], "brand": o["brand"], "price": o["price"], "days": o["days"], "qty": qty})
        if not picks:
            ask = args.get("unclear") or "Что оформить? Напишите деталь и фирму — например «наконечники CTR»."
            return self._done(sid, s, shown or text, ask, "order_unclear", t0, used)
        total = sum(p["price"] * p["qty"] for p in picks)
        lines = ["📦 **Оформляю:**"] + [f"• {p['need']} — {p['brand']} — {_rub(p['price'])} × {p['qty']} = "
                                        f"{_rub(p['price'] * p['qty'])} · {p['days']} дн." for p in picks]
        lines += [f"**Итого: {_rub(total)}**", ""]
        s["order"] = {"picks": picks, "total": total, "t": time.time()}
        phone = s.get("phone")
        lines.append(f"Передал менеджеру — он свяжется по номеру {phone} и подтвердит заказ." if phone else CONTACT_ASK)
        res = self._done(sid, s, shown or text, "\n".join(lines), "order", t0, used)
        res["contact"] = not phone
        await self._notify(sid, s, "🛒 Заказ от Кирилла")
        return res

    async def _more(self, sid: str, s: dict, v, dec: dict, text: str, used: dict, t0: float, shown: str | None) -> dict:
        """«А какие есть?», «а подешевле?», «зепро» — другие предложения по уже подобранному, без нового подбора."""
        last = s["last"]
        want = T.stems(dec.get("more_of") or text)
        idx = [i for i, it in enumerate(last["items"]) if any(T.same(w, x) for w in want for x in T.stems(it["need"]))]
        idx = idx or list(range(len(last["items"])))
        brand = Q.key(dec.get("brand") or "")
        out = []
        for i in idx[:6]:
            it = last["items"][i]
            for c in it["catalog"]:
                rows: list = []
                if c.get("oem"):
                    try:
                        _, rows = await self.eng.offers(c["oem"], v.brand)
                    except Exception:  # noqa: BLE001
                        rows = []
                offers = [self._offer(_offer(r)) for r in rows] or c["offers"]
                if brand:
                    offers = [o for o in offers if Q.key(o["brand"]).startswith(brand[:6])]
                best: dict = {}
                for o in sorted(offers, key=lambda o: (o["price"], o["days"])):
                    best.setdefault(Q.key(o["brand"]), o)
                lines = [f"🔧 **{c['part']}**"]
                if not best:
                    lines.append(f"• {dec.get('brand')} — сейчас нет в продаже по этому номеру")
                for o in list(best.values())[:8]:
                    tag = " ✅ поставщик автозаводов" if o.get("quality") == "OE" else ""
                    lines.append(f"• {o['brand']} — {_rub(o['price'])} · {o['days']} дн.{tag}")
                out.append("\n".join(lines))
        answer = ("\n\n".join(out) or "Других вариантов сейчас нет.") + "\n\nНапишите, какие варианты оформить."
        return self._done(sid, s, shown or text, answer, "more", t0, used)

    async def _numbers(self, sid: str, s: dict, v, numbers: list[str], used: dict, t0: float, text: str) -> dict:
        """«А этот номер 104000HQ?» — что это за деталь, подходит ли машине по каталогу и есть ли среди аналогов."""
        last = (s.get("last") or {}).get("items") or []
        out = []
        for n in numbers[:3]:
            n = re.sub(r"\s+", "", str(n))
            try:
                rows = await self.eng.src.brands(n)
            except Exception:  # noqa: BLE001
                rows = []
            brand, desc = A.what(rows)
            try:
                app = await self.cat.applicability(v, n)
            except Exception:  # noqa: BLE001
                app = None
            mine = next((it["need"] for it in last for c in it["catalog"]
                         if A.norm(n) in {A.norm(x) for x in c.get("numbers") or []}), "")
            what = f"{brand} {n}" + (f" — {desc}" if desc else "")
            if app and app.get("status") == "FULLY":
                out.append(f"✅ {what}: по каталогу подходит вашей машине.")
            elif mine:
                out.append(f"✅ {what}: это аналог к «{mine}», подходит.")
            elif app and app.get("status") == "PARTIAL":
                out.append(f"⚠️ {what}: по каталогу подходит частично — зависит от комплектации, уточню у менеджера.")
            else:
                out.append(f"❓ {what or n}: в каталоге вашей машины и среди аналогов подобранных деталей не нашёл — "
                           "передам менеджеру проверить.")
        return self._done(sid, s, text, "\n".join(out), "number", t0, used)

    async def _notify(self, sid: str, s: dict, title: str):
        """Карточка менеджерам (закрытый бета-тест): кто, машина, что берёт, телефон — если клиент поделился."""
        if self.notify is None:
            return
        o = s.get("order") or {}
        lines = [title, f"Клиент: {s.get('name') or sid}" + (f", {s['phone']}" if s.get("phone") else " (номер не оставил)"),
                 f"Машина: {s.get('car', '')}" + (f", VIN {s['ident']}" if s.get("ident") else "")]
        lines += [f"• {p['need']} — {p['brand']} — {_rub(p['price'])} × {p['qty']}" for p in o.get("picks") or []]
        if o.get("total"):
            lines.append(f"Итого: {_rub(o['total'])}")
        try:
            await self.notify("\n".join(lines))
        except Exception as e:  # noqa: BLE001 — уведомление не должно ронять ответ клиенту
            log.info("notify: %s", type(e).__name__)

    async def photo_text(self, image: bytes, caption: str = "") -> tuple[str, str]:
        """Фото → (текст для подбора, что сказать клиенту). VIN проверяем так же, как набранный (T.find_ident):
        не прошёл — не выдумываем, просим прислать текстом. Само фото не храним."""
        import base64
        url = "data:image/jpeg;base64," + base64.b64encode(image).decode()
        m, _ = await self.gw.chat(self.model, [{"role": "user", "content": [
            {"type": "text", "text": PHOTO_READ}, {"type": "image_url", "image_url": {"url": url}}]}], [T_PHOTO])
        c = (m.get("tool_calls") or [None])[0]
        try:
            d = json.loads(c["function"].get("arguments") or "{}") if c else {}
        except ValueError:
            d = {}
        ident = T.find_ident(" ".join(str(d.get(k) or "") for k in ("vin", "frame")))
        plate = T.find_plate(str(d.get("plate") or "")) if not ident else ""
        if ident or plate:
            return f"{ident or plate} {caption}".strip(), f"📷 Прочитал с фото: {ident or plate}"
        nums = [n for n in d.get("numbers") or [] if A.find(str(n))][:3]
        if nums:
            return (f"{caption} номер {' '.join(nums)}".strip(),
                    f"📷 На детали номер {', '.join(nums)}" + (f" — похоже, {d['part']}" if d.get("part") else ""))
        if d.get("part"):
            return (f"{caption} {d['part']}".strip(),
                    f"📷 Похоже, на фото {d['part']}. Номер на детали не читается — подберу по каталогу вашей машины.")
        if d.get("kind") in ("sts", "pts", "vin_plate"):
            return "", "📷 Не смог уверенно прочитать VIN — пришлите его, пожалуйста, текстом (17 знаков)."
        return "", "📷 Не понял, что на фото. Напишите, пожалуйста, VIN или что нужно текстом."

    def has_phone(self, sid: str) -> bool:
        return bool(self.sessions.load(sid).get("phone"))

    def keep(self, sid: str, text: str):
        """Что клиент написал до номера — ответим, как только поделится (не больше трёх сообщений)."""
        s = self.sessions.load(sid)
        s["before_phone"] = (s.get("before_phone") or [])[-2:] + [text[:2000]]
        self.sessions.save(sid, s)

    async def contact(self, sid: str, phone: str, name: str = "") -> tuple[str, list[str]]:
        """Клиент поделился номером: запомнить; новый клиент и заказ — в группу менеджеров.
        (ответ клиенту, его сообщения до номера — на них отвечаем теперь)."""
        async with self.locks.setdefault(sid, asyncio.Lock()):
            s = self.sessions.load(sid)
            new = not s.get("phone")
            s["phone"], s["name"] = phone, name or s.get("name", "")
            pending = s.pop("before_phone", None) or []
            self.sessions.save(sid, s)
        if s.get("order"):
            await self._notify(sid, s, "📱 Клиент оставил номер по заказу")
            return f"Спасибо! Передал заказ менеджеру — он свяжется по номеру {phone}.", pending
        if new:
            await self._notify(sid, s, "🆕 Новый клиент у Кирилла")
        if pending:
            return "Спасибо! Сейчас отвечу на ваше сообщение.", pending
        return ("Спасибо! Пришлите VIN вашей машины (или госномер) и напишите, что нужно — "
                "можно списком или голосовым."), pending

    async def _pipeline(self, v, tree, dec: dict, items: list[dict], text: str, prefs: dict,
                        used: dict, greet: bool = False, on_part=None) -> tuple[str, dict]:
        car = v.summary()
        if not items:
            # Не про детали: короткий ответ продавца на вопросы и болтовню, без подбора
            data = {"items": [], "questions": dec.get("questions", []), "not_parts": dec.get("not_parts", []),
                    "client": text}
            return await self.sell(car, data, used, prefs, greet), data
        t1 = time.time()
        if on_part is not None:
            await on_part({"start": len(items)})

        async def one(it: dict) -> tuple[dict, str | None]:
            # Деталь целиком: подбор → цены → блок ответа. По частям (26.10, пользователь: «клиент не сидит минуту
            # перед „печатает…“») — готовая деталь уходит клиенту сразу, со схемой узла
            p = await self._safe(self.select(v, tree, it, used), {"found": False})
            pr = await self._safe(self.priced(v, it, p), [])
            # Как выглядит деталь (26.10, пользователь: «картинок деталей нет — печально для восприятия»): фото из прайса
            # СТМ; разбор прайса — не в цикле событий (первая загрузка — секунды)
            photo = await self._safe(asyncio.to_thread(self.stm.photo_for, it["need"], pr[0]["part"], v.brand, v.name),
                                     None) if pr else None
            entry = {"need": it["need"], "axis": it.get("axis"), "qty": it.get("qty"),
                     "certainty": it.get("certainty"), "client_words": it.get("words"), "note": it.get("note", ""),
                     "found": bool(p.get("found")) and bool(pr), "catalog": pr, "group": _group_name(tree, p.get("id")),
                     "separate": bool(it.get("separate")), "brand": it.get("brand") or "", "photo": photo,
                     "reason": p.get("reason", "") if not pr or re.search(r"отдельно|в сборе", p.get("reason", ""), re.I)
                     else "",
                     # Как агенты пришли к детали — для журнала и разбора ошибок (продавцу и клиенту не уходит)
                     "why": {"decoder": it.get("why", ""), "selector": p.get("why", ""),
                             "notes": K.notes("decoder", it.get("words", ""), v.brand)
                             + K.notes("selector", f"{it['need']} {it.get('words', '')}", v.brand)}}
            if on_part is None:
                return entry, None
            block = await self._block(car, entry, used, prefs)
            sch = next((c["scheme"] for c in pr if c.get("scheme")), None)
            await self._safe(on_part({"block": block, "maybe": entry["certainty"] == "maybe", "photo": photo,
                                      "scheme": dict(sch, label=entry["need"][:40]) if sch else None}), None)
            return entry, block
        done = await asyncio.gather(*(one(it) for it in items))
        t2 = time.time()
        data = {"items": [e for e, _ in done], "questions": dec.get("questions", []), "not_parts": dec.get("not_parts", []),
                "streamed": on_part is not None}
        answer = await self.sell_items(car, data, used, prefs, greet,
                                       blocks=[b for _, b in done] if on_part is not None else None)
        # Сколько секунд на этапах — в расход (журнал и /v1/assistant): видно, что тормозит
        used["timing"] = {"parts": round(t2 - t1), "sell": round(time.time() - t2)}
        return answer, data

    def _done(self, sid: str, s: dict, text: str, answer: str, status: str, t0: float,
              used: dict | None = None, data: dict | None = None, buttons: list | None = None) -> dict:
        hist = s.get("history", []) + [{"role": "user", "content": mask(text)[:1500]},
                                       {"role": "assistant", "content": answer[:1500]}]
        s["history"] = hist[-HISTORY:]
        s["updated"] = time.time()
        self.sessions.save(sid, s)
        res = {"text": answer, "status": status, "buttons": buttons or [], "used": used or {}, "data": data or {},
               "seconds": round(time.time() - t0, 1)}
        if self.journal is not None:
            # В тот же журнал, что и робот: страница «Разговоры с роботом» показывает и Кирилла
            self.journal.turn(sid, len(hist) // 2, text, {
                "status": f"assistant:{status}", "seconds": res["seconds"], "text": answer,
                "vehicle": {"summary": s.get("car", "")},
                "positions": [{"query": i["need"], "status": "found" if i["found"] else "not_found",
                               "why": i.get("why") or {}, "group": i.get("group") or ""}
                              for i in (data or {}).get("items", [])]})
        return res


SCHEME_ASK = re.compile(r"схем|где\s+(?:она\s+|он\s+|они\s+)?(?:стоит|стоят|находит|расположен)|покажи|картинк|фото\s+детал", re.I)


# Слова, которые не уточняют, ЧЕЙ это сайлентблок: сторона, ось, верх/низ, «комплект»
_GENERIC = [T.stem(w) for w in ("задний", "передний", "левый", "правый", "нижний", "верхний", "внутренний", "наружный",
                                "комплект", "подвески", "оригинал", "аналог", "отдельно", "штука")]


def _name_score(a: str, b: str) -> float:
    """Похожесть названий по основам слов (0…1): «Комплект торм. колодок суппорта» ≈ «Комплект тормозных колодок…»."""
    x, y = set(T.stems(T.expand(a))), set(T.stems(T.expand(b)))
    if not x or not y:
        return 0.0
    return sum(1 for w in y if any(T.same(w, z) for z in x)) / len(y)


def _match(d, p: dict) -> bool:
    """Та ли это деталь, что выбрал подборщик: та же позиция на схеме и похожее название, или очень похожее название."""
    name, pos = str(p.get("name") or ""), str(p.get("pos") or "").strip()
    if d.name[:70] == name[:70]:
        return True
    sa, sb = T.side(d.name), T.side(name)
    if (sa.lr and sb.lr and sa.lr != sb.lr) or (sa.axis and sb.axis and sa.axis != sb.axis):
        return False   # «правая» — не «левая»: основы слов сторону не различают
    ha, hb = _desc_head(d.name), _desc_head(name)
    if ha and hb and not T.same(ha, hb) and not any(T.same(ha, x) for x in T.stems(T.expand(name))):
        # Главное слово детали должно быть в выбранном названии: «Диск тормозной» — не «Комплект колодок», хоть позиция
        # и совпала; «Комплект торм. колодок» — те же колодки (сокращение сбивает только «главное слово» выбора)
        return False
    score = _name_score(d.name, name)
    return (bool(pos) and str(d.code_on_image) == pos and score >= 0.3) or score >= 0.8


def _why_for(o: dict, part: str) -> dict:
    """Подпись бренда — только если она про эту деталь (26.10, Lexus: «Качество: GMB» к рулевой тяге с подписью
    «подшипники ступиц и помпы»); иначе — только уровень."""
    why = o.get("why") or ""
    if not why or o.get("role") == "оригинал":
        return o
    if any(T.same(a, b) for a in T.stems(why) for b in T.stems(part)):
        return o
    generic = {"OE": "Поставщик автозаводов", "good": "Производитель аналогов"}.get(o.get("quality"), "")
    return dict(o, why=generic)


def _group_name(tree, gid) -> str:
    """Группа каталога, которую выбрал подборщик, — для журнала и стенда («u…» — узел полного каталога)."""
    g = str(gid or "")
    if g[:1] == "u":
        return "узел " + g
    return tree.groups[int(g[1:])].name if tree is not None and g[1:].isdigit() and int(g[1:]) in tree.groups else ""


_HELLO_WORDS = {"здравствуйте", "привет", "добрый", "день", "вечер", "утро", "вот", "вин", "vin", "мой", "моя", "машина",
                "авто", "автомобиль", "номер", "кузова", "госномер", "пожалуйста", "спасибо", "это"}


def _asks(rest: str) -> bool:
    """После VIN есть просьба: хоть одно слово, кроме приветствия и «вот мой вин» (26.10, стенд: «калодка» после VIN
    принималась за «просто VIN» — «Что подобрать?»)."""
    return any(len(w) >= 3 and w.lower() not in _HELLO_WORDS for w in T.words(rest))


def _notes(role: str, text: str, brand: str) -> str:
    """Знания мастера к этой заявке из data/podbor_knowledge.json (раздел notes): только те, чьи слова есть у клиента и
    чья марка — эта машина. Знания о машинах в промптах не пишем — промпт рос бы с каждым случаем (аудит 26.10)."""
    found = K.notes(role, text, brand)
    return "\nЗнания мастера к этой заявке:\n" + "\n".join(f"- {n}" for n in found) if found else ""


def _rub(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ") + " ₽"


def _last(data: dict) -> dict:
    """Прошлый подбор кратко — для «оформляй», «а какие есть», проверки номера: детали, номера и предложения."""
    items = []
    for it in data.get("items") or []:
        if not it.get("found"):
            continue
        items.append({"need": it["need"], "qty": it.get("qty") or 1, "certainty": it.get("certainty"),
                      "catalog": [{"part": c["part"], "oem": c.get("oem", ""), "numbers": c.get("numbers") or [],
                                   "offers": [{k: o.get(k) for k in ("brand", "price", "days", "quality", "role")}
                                              for o in c.get("offers") or []]}
                                  for c in it.get("catalog") or []]})
    return {"items": items[:MAX_ITEMS]}


def _goal_need(prefs: dict) -> tuple[str, str]:
    goal = prefs.get("goal") if prefs.get("goal") in VARIANTS else "optimal"
    need = GOALS[goal] + ("; срочно — в первую очередь то, что привезём за 1–2 дня" if prefs.get("when") == "urgent"
                          else "; можно подождать ради цены и качества" if prefs.get("when") == "wait" else "")
    return goal, need


def _no_schemes(item: dict) -> dict:
    """Продавцу схема, кроссы и «почему» агентов не нужны — лишние токены, а «почему» клиенту не показываем."""
    return dict({k: v for k, v in item.items() if k not in ("why", "photo")},
                catalog=[{k: v for k, v in c.items() if k not in ("scheme", "numbers")} for c in item.get("catalog") or []])


def _schemes(data: dict) -> list[dict]:
    """Схемы найденных деталей прошлого подбора: одна на узел и позицию, не больше 8 — на кнопки."""
    out, seen = [], set()
    for it in data.get("items") or []:
        for c in it.get("catalog") or []:
            sc = c.get("scheme")
            if sc and (sc["unit_id"], sc["code"]) not in seen:
                seen.add((sc["unit_id"], sc["code"]))
                out.append(dict(sc, label=re.sub(r"\s*\(.*?\)", "", c["part"]).strip()[:40]))
    return out[:8]


def _scheme_buttons(sch: list[dict]) -> list:
    """«📐 Название» — по две в ряд."""
    btn = [(f"📐 {x['label'][:28]}", f"sch:{i}") for i, x in enumerate(sch)]
    return [btn[i:i + 2] for i in range(0, len(btn), 2)]


def _questions(items: list[dict], qs: list[str], need_prefs: bool = True) -> str:
    """Перед подбором: что поняли и не больше трёх вопросов — два о потребности (в начале разговора) и уточнения по делу."""
    need = "\n".join(f"• {it['need']}" + (" — под вопросом" if it.get("certainty") == "maybe" else "")
                     for it in items[:MAX_ITEMS])
    asks = (["Ставим надолго или нужно подешевле?", "Когда нужно — срочно или можно подождать?"] if need_prefs else []) + qs
    lines = [f"📝 **Понял, нужно:**\n{need}", "", "❓ **Уточню, чтобы подобрать точно под вас:**"]
    lines += [f"{n}. {q}" for n, q in enumerate(asks, 1)]
    lines += ["", "Ответьте кнопками или текстом — и я подберу." if need_prefs else "Ответьте текстом или нажмите «Подобрать»."]
    return "\n".join(lines)


_GOAL_WORDS = [("quality", r"надолго|качеств|оригинал|хорош|надёжн|надежн"), ("cheap", r"дешев|дёшев|бюджет|эконом"),
               ("optimal", r"оптимал|средн|норм")]


def _prefs_from_text(text: str) -> dict:
    """Ответ текстом на «надолго или подешевле / срочно или подождать» — те же предпочтения, что кнопками."""
    t, out = (text or "").lower(), {}
    for goal, rx in _GOAL_WORDS:
        if re.search(rx, t):
            out["goal"] = goal
            break
    if re.search(r"подожд|не\s+срочн|не\s+горит|можно\s+ждать", t):   # «не срочно» — раньше, чем «срочно»
        out["when"] = "wait"
    elif re.search(r"срочн|быстр|сегодня|завтра|горит", t):
        out["when"] = "urgent"
    return out


def _add(used: dict, u: dict):
    used["in"] = used.get("in", 0) + u.get("in", 0)
    used["out"] = used.get("out", 0) + u.get("out", 0)
    used["calls"] = used.get("calls", 0) + 1
