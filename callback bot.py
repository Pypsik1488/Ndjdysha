"""
Бот для заявок на обратный звонок.

Запуск:  python callback_bot.py
Потом открой в браузере: http://localhost:5000
"""
import csv
import json
import random
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


def bootstrap():
    """Один раз сам доустанавливает flask, playwright и браузер Chromium."""
    marker = Path(__file__).parent / ".installed"
    try:
        import flask  # noqa: F401
        import playwright  # noqa: F401
    except ImportError:
        print(">>> Устанавливаю flask и playwright...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "flask", "playwright"])
    if not marker.exists():
        print(">>> Устанавливаю браузер (2-3 минуты, подожди)...")
        r = subprocess.call([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"])
        if r != 0:  # например, нет sudo: ставим без системных библиотек
            subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        marker.write_text("ok")
        print(">>> Готово.")


bootstrap()

from flask import Flask, jsonify, request, send_from_directory  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = Path(__file__).parent
SHOTS = BASE / "screenshots"
SHOTS.mkdir(exist_ok=True)
DONE_FILE = BASE / "done.json"
LOG_FILE = BASE / "log.csv"

# Пауза между сайтами (секунды), чтобы не выглядеть как спам
DELAY = (15, 40)

PHONE_SEL = [
    'input[type="tel"]',
    'input[name*="phone" i]',
    'input[name*="tel" i]',
    'input[id*="phone" i]',
    'input[placeholder*="телефон" i]',
    'input[placeholder*="номер" i]',
    'input[placeholder*="phone" i]',
    'input[placeholder*="+380" i]',
    'input[placeholder*="+7" i]',
]
NAME_SEL = [
    'input[name*="name" i]',
    'input[id*="name" i]',
    'input[placeholder*="имя" i]',
    'input[placeholder*="ім" i]',
    'input[placeholder*="name" i]',
]
# Тексты кнопок, которые открывают форму (рус / укр / англ)
TRIGGER_TEXTS = [
    # звонок (рус)
    "заказать обратный звонок", "заказать звонок", "обратный звонок", "обратная связь",
    "перезвоните мне", "перезвоните", "перезвон", "позвоните мне", "жду звонка",
    "заказать консультацию", "получить консультацию", "бесплатная консультация",
    "консультац", "оставить заявку", "оставьте заявку", "оставить номер",
    "оставить контакты", "отправить заявку", "подать заявку", "заявк",
    # запись (рус)
    "записаться на прием", "записаться на приём", "записаться на консультацию",
    "записаться онлайн", "онлайн запись", "запись на прием", "запись на приём",
    "записаться", "запись",
    # прочее (рус)
    "узнать стоимость", "узнать цену", "рассчитать стоимость", "получить расчет",
    "получить предложение", "получить прайс", "вызвать мастера", "вызвать",
    "связаться с нами", "свяжитесь с нами", "связаться", "заказать",
    "задать вопрос",
    # укр
    "замовити зворотний дзвінок", "замовити дзвінок", "зворотний дзвінок",
    "зворотній дзвінок", "передзвоніть мені", "передзвоніть", "передзвон",
    "зателефонуйте мені", "зателефонуйте", "чекаю на дзвінок",
    "отримати консультацію", "безкоштовна консультація", "залишити заявку",
    "залиште заявку", "залишити номер", "залишити контакти", "надіслати заявку",
    "записатися на прийом", "записатися на консультацію", "запис на прийом",
    "онлайн запис", "записатися", "запис",
    "дізнатися вартість", "дізнатися ціну", "розрахувати вартість",
    "отримати пропозицію", "зв'язатися з нами", "зв'язатися", "замовити",
    "задати питання",
    # eng
    "request a call back", "request a callback", "request a call", "request call",
    "call me back", "call back", "callback", "call me", "book a call",
    "book an appointment", "book appointment", "book now", "free consultation",
    "get a quote", "get in touch", "contact us", "schedule", "request",
]
SUBMIT_TEXTS = [
    "отправ", "заказ", "перезвон", "жду звонка", "заяв", "запис", "звон", "консульт",
    "получить", "оформ", "узнать", "подтверд", "записат",
    "відправ", "надісл", "замовити", "передзвон", "дзвін", "отримати", "підтверд",
    "send", "submit", "request", "book", "call", "get ", "confirm",
]
COOKIE_TEXTS = ["принять", "согласен", "прийняти", "погоджуюсь", "accept", "agree", "ok"]
SUCCESS_WORDS = [
    "спасибо", "дякуємо", "дякую", "благодар", "заявка принята", "заявка отправлена",
    "заявку прийнято", "заявка прийнята", "ми зв'яжемося", "мы свяжемся", "перезвоним",
    "передзвонимо", "зателефонуємо", "свяжется с вами", "зв'яжеться з вами",
    "успешно", "успішно", "принята", "прийнято", "отправлена", "відправлено",
    "thank you", "thanks", "success", "we will call", "we'll call", "received",
]
COMMENT_RU = "Как можно быстрее перезвоните мне"
COMMENT_UK = "Передзвоніть мені якомога швидше"

app = Flask(__name__)
STATE = {"running": False, "rows": []}
STOP = threading.Event()


def host_of(url):
    return urlparse(url).netloc.lower().replace("www.", "")


def normalize(url):
    url = url.strip()
    if url and not url.startswith("http"):
        url = "https://" + url
    return url


def load_done():
    try:
        return json.loads(DONE_FILE.read_text("utf-8"))
    except Exception:
        return {}


def save_done(d):
    DONE_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")


def first_visible(scope, selectors):
    """Первый видимый элемент по списку селекторов (scope: Page или Locator)."""
    for sel in selectors:
        loc = scope.locator(sel)
        try:
            n = min(loc.count(), 10)
        except Exception:
            continue
        for i in range(n):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    return el
            except Exception:
                pass
    return None


PHONE_LABEL = re.compile(r"телефон|тел\.|phone|номер|мобіл|мобил", re.I)
NAME_LABEL = re.compile(r"имя|ім'я|імʼя|обращаться|звертатися|name|фио|піб|как вас", re.I)
PHONE_XPATH = (
    ".//*[(self::label or self::span or self::div or self::p or self::legend) and "
    "(contains(.,'елефон') or contains(.,'hone') or contains(.,'омер') or contains(.,'обіл')) "
    "and string-length(normalize-space(.))<40]"
    "/following::input[not(@type='hidden') and not(@type='checkbox') "
    "and not(@type='radio') and not(@type='submit')][1]"
)
NAME_XPATH = (
    ".//*[(self::label or self::span or self::div or self::p or self::legend) and "
    "(contains(.,'мя') or contains(.,'бращаться') or contains(.,'вертатися') "
    "or contains(.,'ame') or contains(.,'ФИО')) and string-length(normalize-space(.))<40]"
    "/following::input[not(@type='hidden') and not(@type='checkbox') "
    "and not(@type='radio') and not(@type='submit')][1]"
)


def _visible_in(loc, limit=4):
    try:
        n = min(loc.count(), limit)
    except Exception:
        return None
    for i in range(n):
        try:
            el = loc.nth(i)
            if el.is_visible():
                return el
        except Exception:
            pass
    return None


def find_field(scope, selectors, label_re, xpath):
    """Поле ищем по атрибутам, затем по подписи (label), затем по тексту рядом."""
    el = first_visible(scope, selectors)
    if el:
        return el
    try:
        el = _visible_in(scope.get_by_label(label_re))
        if el:
            return el
    except Exception:
        pass
    try:
        el = _visible_in(scope.locator("xpath=" + xpath))
        if el:
            return el
    except Exception:
        pass
    return None


def find_phone(scope):
    return find_field(scope, PHONE_SEL, PHONE_LABEL, PHONE_XPATH)


def find_name(scope):
    return find_field(scope, NAME_SEL, NAME_LABEL, NAME_XPATH)


def click_by_text(page, texts):
    """Кликает по первой видимой ссылке/кнопке, текст которой содержит одну из фраз."""
    clickable = page.locator(
        "a, button, [role=button], input[type=button], input[type=submit], "
        "[class*=btn], [class*=button]"
    )
    for text in texts:
        loc = clickable.filter(has_text=re.compile(re.escape(text), re.I))
        try:
            n = min(loc.count(), 6)
        except Exception:
            continue
        for i in range(n):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    el.click(timeout=3000)
                    return True
            except Exception:
                pass
    return False


def dismiss_cookies(page):
    try:
        click_by_text(page, COOKIE_TEXTS)
        page.wait_for_timeout(500)
    except Exception:
        pass


CLICKABLE = (
    "a, button, [role=button], input[type=button], input[type=submit], "
    "[class*=btn], [class*=button]"
)


def candidate_buttons(page, texts, limit=16):
    """Видимые кнопки/ссылки с нужным текстом, в порядке приоритета фраз."""
    clickable = page.locator(CLICKABLE)
    out, seen = [], set()
    for text in texts:
        loc = clickable.filter(has_text=re.compile(re.escape(text), re.I))
        try:
            n = min(loc.count(), 5)
        except Exception:
            continue
        for i in range(n):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
                label = (el.inner_text() or "").strip()
                if len(label) > 70:  # большие контейнеры, не кнопки
                    continue
                href = el.get_attribute("href") or ""
                if href.startswith(("tel:", "mailto:")):
                    continue
                box = el.bounding_box() or {}
                key = (round(box.get("x", 0)), round(box.get("y", 0)))
                if key in seen:
                    continue
                seen.add(key)
                out.append(el)
            except Exception:
                pass
        if len(out) >= limit:
            break
    return out


def open_form(page):
    """Если поля телефона не видно — нажимаем кнопки «Заказать звонок», «Записаться» и т.п."""
    start_url = page.url
    for el in candidate_buttons(page, TRIGGER_TEXTS):
        try:
            el.click(timeout=3000)
        except Exception:
            continue
        page.wait_for_timeout(1800)
        if find_phone(page):
            return True
        try:
            if page.url != start_url:  # ушли на другую страницу без формы — возвращаемся
                page.go_back(wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(1200)
            else:
                page.keyboard.press("Escape")
        except Exception:
            pass
    # запасной вариант: кнопки без текста (иконка трубки и т.п.)
    icon_sel = [
        '[class*="callback" i]', '[class*="call-back" i]', '[class*="callme" i]',
        '[class*="zvonok" i]', '[class*="phone-btn" i]', '[class*="call-btn" i]',
        '[class*="fixed-call" i]', 'a[href^="#callback"]', 'a[href*="callback"]',
    ]
    for sel in icon_sel:
        loc = page.locator(sel)
        try:
            n = min(loc.count(), 5)
        except Exception:
            continue
        for i in range(n):
            try:
                el = loc.nth(i)
                if el.is_visible():
                    el.click(timeout=3000)
                    page.wait_for_timeout(2000)
                    if find_phone(page):
                        return True
            except Exception:
                pass
    return False


def has_captcha(page):
    sel = ('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], '
           '.g-recaptcha, .h-captcha, [data-sitekey]')
    try:
        return page.locator(sel).count() > 0
    except Exception:
        return False


def tick_consent(scope):
    """Ставит галочки согласия (обработка данных) внутри формы."""
    boxes = scope.locator('input[type="checkbox"]')
    try:
        n = min(boxes.count(), 5)
    except Exception:
        return
    for i in range(n):
        try:
            cb = boxes.nth(i)
            if not cb.is_checked():
                cb.check(force=True, timeout=1500)
        except Exception:
            pass


def local_now():
    from datetime import timedelta, timezone
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Kyiv")).replace(tzinfo=None)
    except Exception:
        return (datetime.now(timezone.utc) + timedelta(hours=2)).replace(tzinfo=None)


def nearest_slot():
    """Ближайшее разумное время для записи: через ~2 часа, в пределах 09:00-18:00."""
    from datetime import timedelta
    now = local_now()
    t = (now + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    if t.date() != now.date() or t.hour >= 18:
        t = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    elif t.hour < 9:
        t = t.replace(hour=10)
    return t


DAY_SELECTORS = [
    ".flatpickr-day:not(.flatpickr-disabled):not(.prevMonthDay):not(.nextMonthDay)",
    ".ui-datepicker-calendar td:not(.ui-state-disabled):not(.ui-datepicker-other-month) a",
    ".react-datepicker__day:not(.react-datepicker__day--disabled):not(.react-datepicker__day--outside-month)",
    ".datepicker td.day:not(.disabled):not(.old):not(.new)",
    ".datepicker-days td.day:not(.disabled):not(.old):not(.new)",
    ".air-datepicker-cell.-day-:not(.-disabled-):not(.-other-month-)",
    ".pika-button.pika-day:not(.is-disabled)",
    ".xdsoft_date:not(.xdsoft_disabled):not(.xdsoft_other_month)",
    ".daterangepicker td.available:not(.off)",
    "[class*=calendar] [class*=day]:not([class*=disabled]):not([class*=other]):not([class*=past])",
]


def pick_calendar_day(page, want_day):
    """Если открылся календарь — кликает по ближайшему доступному дню."""
    for sel in DAY_SELECTORS:
        loc = page.locator(sel)
        try:
            n = min(loc.count(), 62)
        except Exception:
            continue
        first_ok = None
        for i in range(n):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
                txt = (el.inner_text() or "").strip()
                if first_ok is None:
                    first_ok = el
                if txt == str(want_day):
                    el.click(timeout=2000)
                    return True
            except Exception:
                pass
        if first_ok is not None:
            try:
                first_ok.click(timeout=2000)
                return True
            except Exception:
                pass
    return False


def is_placeholder_option(text):
    return bool(re.match(r"^\s*(выбер|вибер|оберіть|оберите|select|choose|--|—|–|\.\.\.)", text, re.I))


def fill_extras(page, scope, comment):
    """Заполняет остальное в форме: списки, радиокнопки, дату/время, комментарий."""
    slot = nearest_slot()

    # выпадающие списки: первый реальный пункт
    sels = scope.locator("select")
    try:
        ns = min(sels.count(), 8)
    except Exception:
        ns = 0
    for i in range(ns):
        try:
            s = sels.nth(i)
            if not s.is_visible():
                continue
            opts = s.locator("option")
            for j in range(min(opts.count(), 40)):
                o = opts.nth(j)
                val = o.get_attribute("value") or ""
                txt = (o.inner_text() or "").strip()
                if o.get_attribute("disabled") is not None or not val or is_placeholder_option(txt):
                    continue
                s.select_option(index=j, timeout=2000)
                break
        except Exception:
            pass

    # радиокнопки: первая в каждой группе
    radios = scope.locator('input[type="radio"]')
    seen = set()
    try:
        nr = min(radios.count(), 20)
    except Exception:
        nr = 0
    for i in range(nr):
        try:
            r = radios.nth(i)
            nm = r.get_attribute("name") or f"_{i}"
            if nm in seen:
                continue
            seen.add(nm)
            if not r.is_checked():
                r.check(force=True, timeout=1500)
        except Exception:
            pass

    # нативные поля даты/времени
    pickers = scope.locator('input[type="date"], input[type="datetime-local"], input[type="time"]')
    try:
        np_ = min(pickers.count(), 6)
    except Exception:
        np_ = 0
    for i in range(np_):
        try:
            el = pickers.nth(i)
            if not el.is_visible():
                continue
            typ = (el.get_attribute("type") or "").lower()
            mn = el.get_attribute("min") or ""
            if typ == "date":
                val = slot.strftime("%Y-%m-%d")
                if mn and mn > val:
                    val = mn
            elif typ == "time":
                val = mn or slot.strftime("%H:%M")
            else:
                val = slot.strftime("%Y-%m-%dT%H:%M")
                if mn and mn > val:
                    val = mn
            el.fill(val, timeout=2000)
        except Exception:
            pass

    # текстовые поля даты (календарики) и времени
    date_like = scope.locator(
        'input[name*="date" i], input[id*="date" i], input[class*="date" i], '
        'input[placeholder*="дат" i], input[name*="data" i], input[name*="day" i]'
    )
    try:
        nd = min(date_like.count(), 4)
    except Exception:
        nd = 0
    for i in range(nd):
        try:
            el = date_like.nth(i)
            if not el.is_visible() or (el.input_value() or "").strip():
                continue
            if (el.get_attribute("type") or "text").lower() in ("date", "datetime-local", "hidden"):
                continue
            el.click(timeout=2000)
            page.wait_for_timeout(500)
            if not pick_calendar_day(page, slot.day):
                ph = (el.get_attribute("placeholder") or "").lower()
                if "/" in ph:
                    txt = slot.strftime("%d/%m/%Y")
                elif "-" in ph and ph[:4] in ("yyyy", "гггг", "рррр"):
                    txt = slot.strftime("%Y-%m-%d")
                else:
                    txt = slot.strftime("%d.%m.%Y")
                el.fill("")
                el.press_sequentially(txt, delay=30)
            page.wait_for_timeout(300)
        except Exception:
            pass
    time_like = scope.locator(
        'input[name*="time" i], input[id*="time" i], input[placeholder*="время" i], '
        'input[placeholder*="час" i], input[placeholder*="time" i]'
    )
    try:
        nt = min(time_like.count(), 3)
    except Exception:
        nt = 0
    for i in range(nt):
        try:
            el = time_like.nth(i)
            if (not el.is_visible() or (el.input_value() or "").strip()
                    or (el.get_attribute("type") or "text").lower() in ("time", "hidden")):
                continue
            el.fill(slot.strftime("%H:%M"), timeout=2000)
        except Exception:
            pass

    # комментарий
    boxes = scope.locator(
        'textarea, input[name*="comment" i], input[name*="message" i], '
        'input[name*="question" i], input[name*="coment" i], '
        'input[placeholder*="комментар" i], input[placeholder*="коментар" i], '
        'input[placeholder*="сообщен" i], input[placeholder*="повідомлен" i], '
        'input[placeholder*="вопрос" i], input[placeholder*="питання" i]'
    )
    try:
        nb = min(boxes.count(), 4)
    except Exception:
        nb = 0
    for i in range(nb):
        try:
            el = boxes.nth(i)
            if el.is_visible() and not (el.input_value() or "").strip():
                el.fill(comment, timeout=2000)
        except Exception:
            pass


def detect_comment(page):
    """Текст комментария на языке сайта (русский / украинский)."""
    try:
        lang = (page.evaluate("document.documentElement.lang") or "").lower()
        if lang.startswith("uk"):
            return COMMENT_UK
        sample = page.inner_text("body")[:4000]
        if sum(sample.count(ch) for ch in "їєґЇЄҐ") >= 3:
            return COMMENT_UK
    except Exception:
        pass
    return COMMENT_RU


def submit(page, phone_el, scope, has_form):
    if has_form:
        btns = scope.locator('button, input[type="submit"], [role=button]')
        pattern = re.compile("|".join(SUBMIT_TEXTS), re.I)
        try:
            n = min(btns.count(), 15)
        except Exception:
            n = 0
        for i in range(n):
            b = btns.nth(i)
            try:
                if not b.is_visible():
                    continue
                label = (b.inner_text() or "") + " " + (b.get_attribute("value") or "")
                if pattern.search(label):
                    b.click(timeout=3000)
                    return
            except Exception:
                pass
        sub = first_visible(scope, ['[type="submit"]'])
        if sub:
            sub.click(timeout=3000)
            return
    phone_el.press("Enter")


def process_site(browser, url, name, phone):
    """Возвращает (статус, заметка, имя_скриншота)."""
    ctx = browser.new_context(locale="ru-RU", viewport={"width": 1366, "height": 850})
    page = ctx.new_page()
    shot = f"{host_of(url).replace(':', '_')}_{datetime.now():%H%M%S}.png"
    shot_path = str(SHOTS / shot)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        dismiss_cookies(page)

        phone_el = find_phone(page)
        if not phone_el and open_form(page):
            phone_el = find_phone(page)
        if not phone_el:
            page.screenshot(path=shot_path)
            return "форма не найдена", "нужна ручная настройка селекторов", shot

        if has_captcha(page):
            page.screenshot(path=shot_path)
            return "капча", "отправь вручную, бот капчу не обходит", shot

        form = phone_el.locator("xpath=ancestor::form[1]")
        has_form = form.count() > 0
        scope = form if has_form else page
        if not has_form:  # форма без тега <form>, например всплывающее окно
            modal = phone_el.locator(
                "xpath=ancestor::*[@role='dialog' or contains(@class,'modal') "
                "or contains(@class,'popup') or contains(@class,'popap')][1]"
            )
            if modal.count() > 0:
                scope, has_form = modal, True

        comment = detect_comment(page)

        name_el = find_name(scope)
        if name_el:
            name_el.click()
            name_el.fill(name)

        phone_el.click()
        phone_el.fill("")
        phone_el.press_sequentially(phone, delay=40)  # печатаем по символам из-за масок ввода

        if has_form:
            fill_extras(page, scope, comment)
            tick_consent(scope)
        submit(page, phone_el, scope, has_form)

        page.wait_for_timeout(4000)
        page.screenshot(path=shot_path)
        text = page.inner_text("body").lower()
        if any(w in text for w in SUCCESS_WORDS):
            return "отправлено", "", shot
        return "отправлено?", "подтверждения не видно, проверь скриншот", shot
    except Exception as e:
        try:
            page.screenshot(path=shot_path)
        except Exception:
            shot = ""
        return "ошибка", str(e).splitlines()[0][:150], shot
    finally:
        ctx.close()


def log_row(url, status, note):
    new = not LOG_FILE.exists()
    with LOG_FILE.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["время", "сайт", "статус", "заметка"])
        w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), url, status, note])


def run_job(name, phone, urls, ignore_history, show_browser):
    done = load_done()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not show_browser)
            for i, row in enumerate(STATE["rows"]):
                if STOP.is_set():
                    break
                url = row["url"]
                host = host_of(url)
                if host in done and not ignore_history:
                    row["status"] = "пропущен"
                    row["note"] = f"уже отправляли {done[host]}"
                    continue
                row["status"] = "в работе"
                status, note, shot = process_site(browser, url, name, phone)
                row.update(status=status, note=note, shot=shot)
                log_row(url, status, note)
                if status.startswith("отправлено"):
                    done[host] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    save_done(done)
                if i < len(STATE["rows"]) - 1:
                    # ждём паузу, но просыпаемся сразу, если нажали «Стоп»
                    STOP.wait(random.uniform(*DELAY))
            browser.close()
    finally:
        for row in STATE["rows"]:
            if row["status"] in ("в очереди", "в работе") and STOP.is_set():
                row["status"] = "остановлено"
        STATE["running"] = False


@app.post("/stop")
def stop():
    STOP.set()
    return jsonify(ok=True)


@app.post("/start")
def start():
    if STATE["running"]:
        return jsonify(error="Уже работает, дождись окончания"), 400
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    urls = [normalize(u) for u in (data.get("urls") or "").splitlines() if u.strip()]
    if not name or not phone or not urls:
        return jsonify(error="Заполни имя, номер и хотя бы один сайт"), 400
    STOP.clear()
    STATE["running"] = True
    STATE["rows"] = [{"url": u, "status": "в очереди", "note": "", "shot": ""} for u in urls]
    threading.Thread(
        target=run_job,
        args=(name, phone, urls, bool(data.get("ignore")), bool(data.get("show"))),
        daemon=True,
    ).start()
    return jsonify(ok=True)


@app.get("/status")
def status():
    return jsonify(STATE)


@app.get("/shots/<path:fname>")
def shots(fname):
    return send_from_directory(SHOTS, fname)


PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Заявки на звонок</title>
<style>
body{font-family:system-ui,sans-serif;max-width:760px;margin:30px auto;padding:0 16px;color:#222}
input,textarea{width:100%;padding:10px;margin:6px 0 12px;font-size:16px;box-sizing:border-box}
button{padding:14px 26px;font-size:17px;cursor:pointer;margin-right:8px;border-radius:8px}
table{display:block;overflow-x:auto}
label.chk{display:block;margin:4px 0}
table{width:100%;border-collapse:collapse;margin-top:24px}
td,th{border-bottom:1px solid #ddd;padding:8px;text-align:left;font-size:14px;vertical-align:top}
</style></head><body>
<h2>Заявки на обратный звонок</h2>
<label>Имя</label><input id="name" placeholder="Иван">
<label>Номер</label><input id="phone" placeholder="+380501234567">
<label>Сайты (по одному в строке)</label>
<textarea id="urls" rows="8" placeholder="example.com&#10;another-site.ua"></textarea>
<label class="chk"><input type="checkbox" id="show" style="width:auto"> Показывать окно браузера</label>
<label class="chk"><input type="checkbox" id="ignore" style="width:auto"> Отправлять повторно, даже если уже отправляли</label>
<br><button onclick="start()">Запустить</button>
<button onclick="stopJob()" style="background:#c62828;color:#fff;border:0;border-radius:8px">Стоп</button>
<table><thead><tr><th>Сайт</th><th>Статус</th><th>Заметка</th><th></th></tr></thead>
<tbody id="rows"></tbody></table>
<script>
async function start(){
  const body = {
    name: document.getElementById('name').value,
    phone: document.getElementById('phone').value,
    urls: document.getElementById('urls').value,
    show: document.getElementById('show').checked,
    ignore: document.getElementById('ignore').checked
  };
  const r = await fetch('/start', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const j = await r.json();
  if (j.error) alert(j.error);
}
async function stopJob(){
  await fetch('/stop', {method:'POST'});
}
async function poll(){
  const r = await fetch('/status');
  const j = await r.json();
  const tb = document.getElementById('rows');
  tb.textContent = '';
  for (const row of j.rows) {
    const tr = document.createElement('tr');
    for (const v of [row.url, row.status, row.note]) {
      const td = document.createElement('td'); td.textContent = v; tr.appendChild(td);
    }
    const td = document.createElement('td');
    if (row.shot) {
      const a = document.createElement('a');
      a.href = '/shots/' + row.shot; a.target = '_blank'; a.textContent = 'скриншот';
      td.appendChild(a);
    }
    tr.appendChild(td);
    tb.appendChild(tr);
  }
}
setInterval(poll, 2000);
</script></body></html>
"""


@app.get("/")
def index():
    return PAGE


def local_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # пакеты не отправляются, нужен только адрес
        return s.getsockname()[0]
    except Exception:
        return "IP-компьютера"
    finally:
        s.close()


def open_codespace_port():
    """В GitHub Codespaces сам открывает порт 5000 и печатает ссылку для телефона."""
    import os
    name = os.environ.get("CODESPACE_NAME")
    if not name:
        return
    domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
    url = f"https://{name}-5000.{domain}/"
    ok = False
    for _ in range(6):  # порт появляется не сразу, пробуем несколько раз
        time.sleep(3)
        r = subprocess.run(
            ["gh", "codespace", "ports", "visibility", "5000:public", "-c", name],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            ok = True
            break
        err = (r.stderr or r.stdout).strip()
    print("\n" + "=" * 50)
    print("ОТКРОЙ НА ТЕЛЕФОНЕ ЭТУ ССЫЛКУ:")
    print(url)
    if not ok:
        print("(порт открыть автоматически не вышло:", err[:150], ")")
    print("=" * 50 + "\n")


@app.after_request
def add_headers(resp):
    if resp.mimetype == "text/html":
        resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["Cache-Control"] = "no-store"
    return resp


if __name__ == "__main__":
    threading.Thread(target=open_codespace_port, daemon=True).start()
    print("На компьютере:  http://localhost:5000")
    app.run(host="0.0.0.0", port=5000)
