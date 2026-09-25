"""Проверка API без настоящего ABCP: запросы уходят в заглушку MockTransport."""
import hashlib
import tempfile

import httpx
import pytest
from fastapi.testclient import TestClient

from app import tokens
from app.abcp import Abcp
from app.config import Settings
from app.main import RateLimiter, create_app

S = Settings(abcp_host="https://abcp.test", admin_login="admin", admin_md5="a" * 32, token_secret=b"s" * 40,
             articles_info_per_day=100, guest_profile_id="156077169",
             state_dir=tempfile.mkdtemp(prefix="avtodrug-test-"))  # /var/lib/… в CI недоступна
GOOD_MD5 = hashlib.md5(b"secret").hexdigest()


def fake_abcp(request: httpx.Request) -> httpx.Response:
    p = request.url.path.strip("/")
    q = dict(request.url.params)
    admin = q.get("userlogin") == "admin" and q.get("userpsw") == "a" * 32
    if p == "user/info":
        if q.get("userlogin") == "ivan" and q.get("userpsw") == GOOD_MD5:
            return httpx.Response(200, json={"id": "101", "name": "Иван Петров"})
        return httpx.Response(403, json={"errorCode": 102, "errorMessage": "Wrong name or password!"})
    if p == "cp/user/new":
        f = dict(httpx.QueryParams(request.content.decode()))
        assert f.get("userlogin") == "admin"  # регистрация — от имени API-администратора
        if f.get("mobile") == "79780000000":
            return httpx.Response(200, json={"status": 0, "errorMessage": {"mobile": "Номер уже зарегистрирован"}})
        assert f["office"] == "27993" and f["marketType"] == "1"
        return httpx.Response(200, json={"status": 1, "userCode": "555"})
    if p == "user/restore":
        f = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(200, json={"status": 2 if f.get("code") else 1, "message": "ok"})
    assert admin, f"админский запрос {p} без админского доступа"
    if p == "cp/users" and ("phone" in q or "email" in q):
        found = q.get("phone") == "79780000009" or q.get("email") == "ivan@test.ru"
        return httpx.Response(200, json=[{"userId": "101"}] if found else [])
    if p == "cp/users":
        # как у ABCP: объект по индексам
        return httpx.Response(200, json={"0": {"userId": "101", "name": "Иван", "surname": "Петров", "mobile": "79781112233", "balance": "0", "debt": "1826.50",
                                               "saldo": "1826.5", "creditLimit": "5000", "profileId": "7"}})
    if p == "cp/users/profiles":
        return httpx.Response(200, json=[{"profileId": "7", "name": "Опт2"}])
    if p == "cp/order":
        orders = {
            "5001": {"number": "5001", "userId": "101", "debt": "1826", "paid": "0"},
            "5002": {"number": "5002", "userId": "999", "debt": "500", "paid": "0"},  # чужой
            "5003": {"number": "5003", "userId": "101", "debt": "0", "paid": "1"},
        }
        o = orders.get(q.get("number"))
        return httpx.Response(200, json=o) if o else httpx.Response(404, json={"errorCode": 301, "errorMessage": "Не найден"})
    if p == "cp/payment/token":
        return httpx.Response(200, json={"paymentLink": f"https://pay.test/{q['number']}"})
    if p == "cp/payment/top-balance-link":
        return httpx.Response(200, json={"paymentLink": f"https://pay.test/topup/{q['clientId']}/{q['amount']}"})
    if p == "search/brands":
        return httpx.Response(200, json={"0": {"brand": "Knecht", "number": "OC90", "numberFix": "OC90",
                                               "availability": "1", "priceIn": "999"}})
    if p == "search/articles":
        assert q.get("profileId") == "156077169"
        return httpx.Response(200, json=[{"brand": "Knecht", "number": "OC90", "numberFix": "OC90", "price": "320",
                                          "priceIn": "150", "priceRate": "1", "distributorId": "77",
                                          "availability": "6", "deliveryPeriod": "0", "itemKey": "k"}])
    if p == "articles/info" and q.get("format") == "bnc":
        return httpx.Response(200, json=[{"brand": q["brand"], "number": q["number"], "crosses": [
            {"brand": "Zekkert", "number": "OF-4063", "numberFix": "OF4063", "reliable": True},
            {"brand": "Noname", "number": "X1", "numberFix": "X1", "reliable": False}]}])
    if p == "articles/info":
        return httpx.Response(200, json=[{"brand": q["brand"], "number": q["number"],
                                          "images": [{"name": "abc0002.jpeg"}, "https://x.test/b.jpg"]}])
    return httpx.Response(404, json={"errorMessage": "unknown"})


@pytest.fixture
def client():
    app = create_app(S, Abcp(S, transport=httpx.MockTransport(fake_abcp)))
    with TestClient(app) as c:
        yield c


def login(c) -> dict:
    r = c.post("/v1/session", json={"login": "ivan", "passwordMd5": GOOD_MD5})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_login_ok_and_wrong(client):
    assert client.post("/v1/session", json={"login": "ivan", "passwordMd5": "0" * 32}).status_code == 401
    h = login(client)
    assert h["Authorization"].startswith("Bearer ")


def test_endpoints_need_token(client):
    assert client.get("/v1/me/finance").status_code == 401
    assert client.get("/v1/me/finance", headers={"Authorization": "Bearer fake.token"}).status_code == 401


def test_finance(client):
    f = client.get("/v1/me/finance", headers=login(client)).json()
    assert f["debt"] == 1826.5 and f["creditLimit"] == 5000 and f["profile"] == "Опт2"


def test_pay_only_own_unpaid_order(client):
    h = login(client)
    r = client.get("/v1/orders/5001/pay", headers=h)
    assert r.status_code == 200 and r.json()["url"] == "https://pay.test/5001"
    assert client.get("/v1/orders/5002/pay", headers=h).status_code == 404  # чужой заказ
    assert client.get("/v1/orders/5003/pay", headers=h).status_code == 409  # уже оплачен
    assert client.get("/v1/orders/abc/pay", headers=h).status_code == 400


def test_topup(client):
    h = login(client)
    assert client.get("/v1/topup?amount=1500", headers=h).json()["url"] == "https://pay.test/topup/101/1500.00"
    assert client.get("/v1/topup?amount=0", headers=h).status_code == 400


def test_images(client):
    r = client.post("/v1/images", headers=login(client), json={"items": [{"brand": "Knecht", "number": "OC90"}]})
    assert r.json()["Knecht|OC90"] == ["https://imgcdn.abcp.ru/p/abc0002.jpeg", "https://x.test/b.jpg"]


def test_token_tamper_and_expiry():
    t = tokens.issue(b"k" * 40, "101", ttl=60, now=1000)
    assert tokens.verify(b"k" * 40, t, now=1030) == "101"
    assert tokens.verify(b"k" * 40, t, now=2000) is None           # просрочен
    assert tokens.verify(b"x" * 40, t, now=1030) is None           # чужой секрет
    payload, sig = t.split(".")
    assert tokens.verify(b"k" * 40, payload[:-2] + "AA." + sig, now=1030) is None  # подменён


def test_rate_limit():
    rl = RateLimiter(limit=3, window=60)
    assert all(rl.allow("ip", now=100 + i) for i in range(3))
    assert not rl.allow("ip", now=110)
    assert rl.allow("ip", now=200)


def test_reliable(client):
    r = client.post("/v1/reliable", headers=login(client), json={"brand": "Knecht", "number": "OC90"})
    assert r.json()["reliable"] == ["ZEKKERT|OF4063"]


def test_register(client):
    ok = {"name": "Иван", "mobile": "+7 (978) 123-45-67", "password": "Secret123", "office": "27993"}
    assert client.post("/v1/register", json=ok).json() == {"ok": True, "needsActivation": False}
    dup = client.post("/v1/register", json={**ok, "mobile": "79780000000"})
    assert dup.status_code == 400 and "уже зарегистрирован" in dup.json()["detail"]
    assert client.post("/v1/register", json={**ok, "office": "1; DROP"}).status_code == 422


def test_restore(client):
    assert client.post("/v1/restore", json={"emailOrMobile": "79781234567"}).json()["ok"]
    assert client.post("/v1/restore", json={"emailOrMobile": "79781234567", "code": "1234", "passwordNew": "newpass"}).json()["ok"]


def test_articles_info_budget():
    import asyncio
    s0 = Settings(abcp_host="https://abcp.test", admin_login="admin", admin_md5="a" * 32, token_secret=b"s" * 40)
    a = Abcp(s0, transport=httpx.MockTransport(fake_abcp))
    assert asyncio.run(a.images("Knecht", "OC90")) == []  # по умолчанию выключено — в ABCP не ходим
    s2 = Settings(abcp_host="https://abcp.test", admin_login="admin", admin_md5="a" * 32, token_secret=b"s" * 40,
                  articles_info_per_day=1)
    b = Abcp(s2, transport=httpx.MockTransport(fake_abcp))
    assert asyncio.run(b.images("Knecht", "OC90"))            # первый — можно
    assert asyncio.run(b.reliable_crosses("Knecht", "OC90")) == []  # лимит суток исчерпан


def test_guest_search_hides_purchase_price(client):
    b = client.get("/v1/guest/brands?number=OC90").json()
    assert b == [{"brand": "Knecht", "number": "OC90", "numberFix": "OC90", "availability": "1"}]
    o = client.get("/v1/guest/offers?number=OC90&brand=Knecht").json()[0]
    assert o["price"] == "320"
    for secret in ("priceIn", "priceRate", "distributorId", "itemKey"):
        assert secret not in o


def test_guest_search_disabled_without_profile():
    s0 = Settings(abcp_host="https://abcp.test", admin_login="admin", admin_md5="a" * 32, token_secret=b"s" * 40)
    app = create_app(s0, Abcp(s0, transport=httpx.MockTransport(fake_abcp)))
    with TestClient(app) as c:
        assert c.get("/v1/guest/brands?number=OC90").status_code == 403


def test_numbers_with_thousand_separators():
    from app.abcp import _num
    assert _num("4 630,00") == 4630.0          # как в cp/users у реального клиента
    assert _num("-4 630,00") == -4630.0   # неразрывный пробел
    assert _num("4630.00") == 4630.0 and _num(4630) == 4630.0
    assert _num(None) == 0.0 and _num("") == 0.0


def test_app_update(tmp_path):
    from dataclasses import replace
    s = replace(S, app_dir=str(tmp_path), app_upload_token="t" * 40)
    with TestClient(create_app(s, Abcp(s, transport=httpx.MockTransport(fake_abcp)))) as c:
        assert c.get("/v1/app/latest").status_code == 404
        apk = b"PK\x03\x04" + b"x" * 100
        assert c.put("/v1/app/apk/5", content=apk).status_code == 403  # без токена
        assert c.put("/v1/app/apk/5", content=b"<html>", headers={"X-Upload-Token": "t" * 40}).status_code == 400
        for code in (5, 7, 6, 8):  # 6 — перезапуск старой сборки, «последнюю» не откатывает
            r = c.put(f"/v1/app/apk/{code}?versionName=1.{code}&notes=fix", content=apk,
                      headers={"X-Upload-Token": "t" * 40})
            assert r.status_code == 200, r.text
        info = c.get("/v1/app/latest").json()
        assert info["versionCode"] == 8 and info["url"] == "/v1/app/apk/8" and info["size"] == len(apk)
        assert c.get("/v1/app/apk/8").content == apk
        assert c.get("/v1/app/apk/5").status_code == 404  # старые чистятся, держим 3


def test_laximo_proxy():
    from dataclasses import replace
    from app.laximo import Laximo
    seen = {}

    def fake_laximo(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        if request.url.params.get("identString") == "BAD":
            return httpx.Response(200, json={"message": "E_INVALIDREQUEST:bad"})
        return httpx.Response(200, json=[{"catalog": "TOYOTA", "ssd": "$abc$"}])

    s = replace(S, laximo_user="lx", laximo_pass="pw")
    lx = Laximo(s, transport=httpx.MockTransport(fake_laximo))
    with TestClient(create_app(s, Abcp(s, transport=httpx.MockTransport(fake_abcp)), lx)) as c:
        r = c.post("/v1/laximo/findVehicle", json={"identString": "JT123"})
        assert r.status_code == 200 and r.json()[0]["catalog"] == "TOYOTA"
        assert seen["path"].endswith("/restApi/v1/findVehicle") and seen["params"] == {"identString": "JT123"}
        assert seen["auth"].startswith("Basic ")  # пароль добавляет сервер
        assert "E_INVALIDREQUEST" in c.post("/v1/laximo/findVehicle", json={"identString": "BAD"}).text
        assert c.post("/v1/laximo/deleteEverything", json={}).status_code == 404
        assert c.post("/v1/laximo/findVehicle", json={"userlogin": "x"}).status_code == 400
        r = c.post("/v1/laximo/listUnits", json={"ssd": "$a%2B$", "catalog": "T"}, headers=login(c))
        assert r.status_code == 200 and seen["params"]["ssd"] == "$a%2B$"


def test_laximo_off_without_credentials(client):
    assert client.post("/v1/laximo/findVehicle", json={"identString": "X"}).status_code == 503



def test_access_request_queue(tmp_path):
    from dataclasses import replace
    sent = []

    def fake(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.telegram.org":
            sent.append(__import__("json").loads(request.content)["text"])
            return httpx.Response(200, json={"ok": True})
        return fake_abcp(request)

    s = replace(S, state_dir=str(tmp_path), telegram_bot_token="t", telegram_chat_id="-100")
    with TestClient(create_app(s, Abcp(s, transport=httpx.MockTransport(fake)))) as c:
        assert c.post("/v1/access-request", json={"missing": ["brands"]}).status_code == 401
        h = login(c)
        assert c.get("/v1/access-request", headers=h).json()["pending"] is False
        r = c.post("/v1/access-request", headers=h, json={"missing": ["brands", "basket", "hack"]})
        assert r.status_code == 200 and r.json()["notified"] is True
        assert "101" in sent[0] and "hack" not in sent[0]
        assert "79781112233" not in sent[0] and "Петров" not in sent[0]  # телефонов и имён в Telegram не шлём
        assert "79781112233" not in (tmp_path / "access_requests.json").read_text(encoding="utf-8")
        c.post("/v1/access-request", headers=h, json={"missing": ["brands"]})
        assert len(sent) == 1  # повтор сразу — без второго сообщения
        assert c.get("/v1/access-request", headers=h).json()["pending"] is True
        assert c.delete("/v1/access-request", headers=h).json()["closed"] is True
        assert "✅" in sent[-1] and c.get("/v1/access-request", headers=h).json()["pending"] is False



def test_register_existing_client_offers_login(client):
    r = client.post("/v1/register", json={"name": "Иван", "mobile": "+7 978 000-00-09", "password": "Pass12345", "office": "27993"})
    assert r.status_code == 409 and "уже зарегистрирован" in r.json()["detail"]
    r = client.post("/v1/register", json={"name": "Иван", "mobile": "79781111111", "email": "ivan@test.ru",
                                          "password": "Pass12345", "office": "27993"})
    assert r.status_code == 409


def test_abcp_html_error_is_plain_text():
    from app.abcp import _msg
    assert _msg('<div class="fr-alert">Не найден&nbsp;пользователь</div>') == "Не найден пользователь"



def test_access_request_not_marked_notified_when_telegram_fails(tmp_path):
    from dataclasses import replace
    s = replace(S, state_dir=str(tmp_path))  # бот не настроен
    with TestClient(create_app(s, Abcp(s, transport=httpx.MockTransport(fake_abcp)))) as c:
        h = login(c)
        assert c.post("/v1/access-request", headers=h, json={"missing": ["brands"]}).json()["notified"] is False
        # Бота настроили — повторная заявка сразу уходит менеджерам, а не через 12 часов
        from app.access import AccessQueue
        assert "notifiedAt" not in AccessQueue(tmp_path).get("101")



def test_push_diff_and_text():
    from app.push import diff_orders, push_text
    known = {}
    o = [{"userId": "101", "number": "5001", "positions": [
        {"id": 1, "brand": "Knecht", "number": "OC90", "status": "В работе"},
        {"id": 2, "brand": "Mann", "number": "W712", "status": "В работе"}]},
         {"userId": "999", "number": "6000", "positions": [{"id": 9, "status": "Выдано"}]}]
    assert diff_orders(o, known, {"101"}, 1.0) == {}  # первое знакомство — без уведомлений
    assert "9" not in known  # чужие (без подписки) не храним
    o[0]["positions"][0]["status"] = "В пути"
    ch = diff_orders(o, known, {"101"}, 2.0)
    assert ch == {"101": {"5001": [("Knecht OC90", "В пути")]}}
    assert push_text("5001", ch["101"]["5001"]) == ("Заказ № 5001: В пути", "Knecht OC90")


def test_push_token_and_watch(tmp_path):
    from dataclasses import replace
    pushes = []
    orders = {"status": "В работе"}

    def fake(request: httpx.Request) -> httpx.Response:
        if request.url.host == "push.test":
            pushes.append(__import__("json").loads(request.content))
            return httpx.Response(200, json={})
        if request.url.path.strip("/") == "cp/orders":
            return httpx.Response(200, json=[{"userId": "101", "number": "5001", "positions": [
                {"id": 1, "brand": "Knecht", "number": "OC90", "status": orders["status"]}]}])
        return fake_abcp(request)

    s = replace(S, state_dir=str(tmp_path), rustore_project_id="p", rustore_push_token="t",
                rustore_push_host="https://push.test", order_watch_interval=0, app_upload_token="u" * 40)
    with TestClient(create_app(s, Abcp(s, transport=httpx.MockTransport(fake)))) as c:
        h = login(c)
        assert c.post("/v1/push/token", json={"token": "device-token-1"}).status_code == 401
        assert c.post("/v1/push/token", headers=h, json={"token": "device-token-1"}).json()["push"] is True
        # статус сменился в ABCP → следующая проверка шлёт push
        orders["status"] = "В пути"
        sent = c.portal.call(c.app.state.watcher.tick)
        assert sent == 1 and pushes[-1]["message"]["token"] == "device-token-1"
        assert pushes[-1]["message"]["data"]["title"] == "Заказ № 5001: В пути"
        assert __import__("json").loads(pushes[-1]["message"]["data"]["items"]) == [["Knecht OC90", "В пути"]]
        r = c.post("/v1/admin/push-test", json={"uid": "101"}, headers={"X-Upload-Token": "u" * 40})
        assert r.json() == {"devices": 1, "codes": [200]}
        assert c.post("/v1/admin/push-test", json={"uid": "101"}).status_code == 403
        c.request("DELETE", "/v1/push/token", json={"token": "device-token-1"})
        assert c.post("/v1/admin/push-test", json={"uid": "101"}, headers={"X-Upload-Token": "u" * 40}).json()["devices"] == 0




def test_manager_button_grants_access_and_pushes(tmp_path):
    from dataclasses import replace
    from app.access import ButtonListener
    tg, pushes = [], []

    def fake(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.telegram.org":
            tg.append((request.url.path.rsplit("/", 1)[-1], request.content))
            return httpx.Response(200, json={"ok": True, "result": []})
        if request.url.host == "push.test":
            pushes.append(__import__("json").loads(request.content))
            return httpx.Response(200, json={})
        if request.url.path.strip("/") == "cp/orders":
            return httpx.Response(200, json=[])
        return fake_abcp(request)

    s = replace(S, state_dir=str(tmp_path), telegram_bot_token="t", telegram_chat_id="-100",
                rustore_project_id="p", rustore_push_token="t", rustore_push_host="https://push.test", order_watch_interval=0)
    with TestClient(create_app(s, Abcp(s, transport=httpx.MockTransport(fake)))) as c:
        h = login(c)
        c.post("/v1/push/token", headers=h, json={"token": "device-token-1"})
        c.post("/v1/access-request", headers=h, json={"missing": ["brands"]})
        assert b"granted:101" in [b for m, b in tg if m == "sendMessage"][-1]  # кнопка в сообщении
        buttons: ButtonListener = c.app.state.buttons
        # нажатие не из нашего чата игнорируем
        c.portal.call(buttons.handle, {"callback_query": {"id": "1", "data": "granted:101", "message": {"chat": {"id": 666}}}})
        assert pushes == [] and c.get("/v1/access-request", headers=h).json()["pending"] is True
        c.portal.call(buttons.handle, {"callback_query": {"id": "2", "data": "granted:101", "from": {"first_name": "Оля"},
                                                          "message": {"chat": {"id": -100}, "message_id": 5, "text": "заявка"}}})
        assert pushes[-1]["message"]["data"]["type"] == "access_granted"
        assert c.get("/v1/access-request", headers=h).json()["pending"] is False
        assert any(m == "editMessageText" and "Оля".encode() in b for m, b in tg)



def test_app_note_only_own_order_and_once(tmp_path):
    from dataclasses import replace
    posted = []
    notes = []

    def fake(request: httpx.Request) -> httpx.Response:
        p = request.url.path.strip("/")
        if p == "cp/order" and request.method == "POST":
            f = dict(httpx.QueryParams(request.content.decode()))
            assert "order[positions][0][id]" not in f  # позиции не трогаем
            posted.append(f)
            notes.append({"value": f["order[notes][0][value]"]})
            return httpx.Response(200, json={"number": f["order[number]"]})
        if p == "cp/order":
            q = dict(request.url.params)
            owner = {"5001": "101", "5002": "999"}.get(q.get("number"))
            return httpx.Response(200, json={"number": q.get("number"), "userId": owner, "notes": notes})
        return fake_abcp(request)

    s = replace(S, state_dir=str(tmp_path))
    with TestClient(create_app(s, Abcp(s, transport=httpx.MockTransport(fake)))) as c:
        h = {**login(c), "X-App-Version": "1.0.55"}
        assert c.post("/v1/orders/5002/app-note", headers=h).status_code == 404  # чужой
        assert c.post("/v1/orders/5001/app-note", headers=h).json() == {"added": True}
        assert "приложение" in posted[0]["order[notes][0][value]"] and "1.0.55" in posted[0]["order[notes][0][value]"]
        assert c.post("/v1/orders/5001/app-note", headers=h).json() == {"added": False}  # второй раз — нет
        assert len(posted) == 1



def test_bot_broadcast_with_confirmation(tmp_path):
    from dataclasses import replace
    tg, pushes = [], []

    def fake(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.telegram.org":
            tg.append((request.url.path.rsplit("/", 1)[-1], __import__("json").loads(request.content or b"{}")))
            return httpx.Response(200, json={"ok": True, "result": []})
        if request.url.host == "push.test":
            pushes.append(__import__("json").loads(request.content))
            return httpx.Response(200, json={})
        if request.url.path.strip("/") == "cp/orders":
            return httpx.Response(200, json=[])
        return fake_abcp(request)

    s = replace(S, state_dir=str(tmp_path), telegram_bot_token="t", telegram_chat_id="-100",
                rustore_project_id="p", rustore_push_token="t", rustore_push_host="https://push.test", order_watch_interval=0)
    with TestClient(create_app(s, Abcp(s, transport=httpx.MockTransport(fake)))) as c:
        c.post("/v1/push/token", headers=login(c), json={"token": "device-token-1"})
        b = c.app.state.buttons
        # чужой чат — игнор
        c.portal.call(b.handle, {"message": {"chat": {"id": 1}, "text": "/push Взлом"}})
        assert not any(m == "sendMessage" and "Взлом" in str(j) for m, j in tg)
        c.portal.call(b.handle, {"message": {"chat": {"id": -100}, "text": "/push Скидка 10% на масла"}})
        preview = [j for m, j in tg if m == "sendMessage"][-1]
        assert "Скидка 10%" in preview["text"] and pushes == []  # без подтверждения не шлём
        bid = preview["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        c.portal.call(b.handle, {"callback_query": {"id": "1", "data": bid, "message": {"chat": {"id": -100}, "message_id": 7, "text": "p"}}})
        assert pushes[-1]["message"]["data"]["body"] == "Скидка 10% на масла"
        assert pushes[-1]["message"]["data"]["type"] == "promo"
        c.portal.call(b.handle, {"callback_query": {"id": "2", "data": bid, "message": {"chat": {"id": -100}, "message_id": 7, "text": "p"}}})
        assert len(pushes) == 1  # повторное нажатие не шлёт второй раз
        c.portal.call(b.handle, {"message": {"chat": {"id": -100}, "text": "/stats"}})
        assert "Устройств: 1" in [j for m, j in tg if m == "sendMessage"][-1]["text"]



def test_special_push_texts():
    from datetime import datetime
    from app.push import MSK, special_push
    tue_10 = datetime(2026, 9, 29, 10, 0, tzinfo=MSK)
    sat_18 = datetime(2026, 9, 26, 18, 0, tzinfo=MSK)
    p = special_push("123", [("Knecht OC90", "Готово к выдаче")], {"deliveryOfficeId": "27993"}, tue_10)
    assert p["kind"] == "ready" and p["title"] == "Заказ № 123 готов к выдаче 🎉"
    assert p["body"] == "можно забирать: ул. Хрусталёва, 111 · сегодня до 19:00" and p["address"] == "ул. Хрусталёва, 111"
    p = special_push("123", [("A", "Готово к выдаче"), ("B", "В пути (товар заказан)")], {"deliveryOfficeId": "60602"}, sat_18)
    assert p["body"].startswith("Часть заказа") and "завтра с 9:00" in p["body"]
    assert special_push("1", [("A", "Ожидает оплаты")], {})["kind"] == "pay"
    assert special_push("1", [("A", "Задерживается")], {})["kind"] == "delay"
    assert special_push("1", [("A", "В пути (товар заказан)")], {}) is None



def test_confirm_counts_table():
    from app.abcp import confirm_counts
    r = lambda b, n, p, **kw: {"brand": b, "number": n, "distributorId": p, **kw}
    assert confirm_counts([r("MANN", "OC90", "A"), r("MANN", "OC90", "B")]) == [2, 2]
    assert confirm_counts([r("MANN", "OC90", "A"), r("MANN", "OC90", "A")]) == [1, 1]          # один поставщик, 2 склада
    assert confirm_counts([r("MANN", "OC-90", "A"), r("mann", "oc90", "B")]) == [2, 2]         # нормализация
    assert confirm_counts([r("MANN", "OC90", "A"), r("MANN-FILTER", "OC90", "B")]) == [2, 2]   # алиас
    assert confirm_counts([r("", "OC90", "A"), r("", "OC90", "B")]) == [0, 0]                  # пустой бренд
    assert confirm_counts([r("M", "1", "A"), r("M", "1", "B"), r("M", "1", "C"), r("X", "2", "D")]) == [3, 3, 3, 1]
    own = dict(deliveryPeriod="0")
    # Хрусталёва и ПОР — разные distributorId, но это склады АвтоДруг: один поставщик
    assert confirm_counts([r("Z", "OF4063", "1591411", deadlineReplace="Хрусталева 111 (самовывоз)", **own),
                           r("Z", "OF4063", "1791689", deadlineReplace="ПОР20 (самовывоз)", **own)]) == [1, 1]



def test_brand_warranty(client):
    d = client.get("/v1/brands/warranty").json()
    z = d["brands"]["ZEKKERT"]
    assert z["rating"] == 4.5 and "1 год" in z["warranty"] and d["page"].endswith("/garantija")
    assert any(c["url"].startswith("http") for b in d["brands"].values() for c in b["conditions"])



def test_bitrix_chat_flow(tmp_path):
    from dataclasses import replace
    import json as J
    calls, pushes = [], []

    def fake(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "bitrix.freno.ru":
            method = path.rsplit("/", 1)[-1].removesuffix(".json")
            body = J.loads(request.content or b"{}")
            calls.append((method, body))
            if method == "app.info":
                return httpx.Response(200, json={"result": {"CODE": "local.app1"}})
            return httpx.Response(200, json={"result": True})
        if host == "push.test":
            pushes.append(J.loads(request.content))
            return httpx.Response(200, json={})
        if path.strip("/") == "cp/orders":
            return httpx.Response(200, json=[])
        return fake_abcp(request)

    s = replace(S, state_dir=str(tmp_path), bitrix_client_id="local.app1", bitrix_client_secret="sec",
                rustore_project_id="p", rustore_push_token="t", rustore_push_host="https://push.test", order_watch_interval=0)
    with TestClient(create_app(s, Abcp(s, transport=httpx.MockTransport(fake)))) as c:
        h = login(c)
        assert c.get("/v1/chat/status").json() == {"enabled": False}
        # чужой портал не принимаем
        assert c.post("/v1/bitrix/install", data={"auth[domain]": "evil.bitrix24.ru", "auth[access_token]": "x"}).status_code == 403
        r = c.post("/v1/bitrix/install", data={
            "event": "ONAPPINSTALL", "auth[domain]": "bitrix.freno.ru", "auth[access_token]": "AT", "auth[refresh_token]": "RT",
            "auth[expires_in]": "3600", "auth[client_endpoint]": "https://bitrix.freno.ru/rest/", "auth[application_token]": "APPTOK"})
        assert r.status_code == 200, r.text
        assert [m for m, _ in calls][:3] == ["app.info", "imconnector.register", "event.bind"]
        r = c.post("/v1/bitrix/placement", data={"DOMAIN": "bitrix.freno.ru", "PLACEMENT_OPTIONS": '{"LINE":"7","ACTIVE_STATUS":0}'})
        assert r.status_code == 200 and any(m == "imconnector.activate" and b["LINE"] == 7 for m, b in calls)
        assert c.get("/v1/chat/status").json() == {"enabled": True}
        c.post("/v1/push/token", headers=h, json={"token": "device-token-1"})
        # клиент пишет → в открытую линию с именем клиента
        assert c.post("/v1/chat/send", headers=h, json={"text": "Нужен фильтр на Polo"}).status_code == 200
        sent = [b for m, b in calls if m == "imconnector.send.messages"][-1]
        assert sent["MESSAGES"][0]["chat"]["id"] == "101" and sent["MESSAGES"][0]["message"]["text"] == "Нужен фильтр на Polo"
        # событие без правильного application_token — отказ
        assert c.post("/v1/bitrix/event", data={"event": "ONIMCONNECTORMESSAGEADD", "auth[application_token]": "bad"}).status_code == 403
        ev = {"event": "ONIMCONNECTORMESSAGEADD", "auth[application_token]": "APPTOK", "data[LINE]": "7",
              "data[MESSAGES][0][im][chat_id]": "1807", "data[MESSAGES][0][im][message_id]": "86497",
              "data[MESSAGES][0][message][text]": "[b]Светлана:[/b] [br]Есть Knecht OC90, 320 ₽",
              "data[MESSAGES][0][chat][id]": "101"}
        assert c.post("/v1/bitrix/event", data=ev).status_code == 200
        c.post("/v1/bitrix/event", data=ev)  # повтор события — без дубля
        msgs = c.get("/v1/chat/messages", headers=h).json()["messages"]
        assert [(m["dir"], m["text"]) for m in msgs] == [("in", "Нужен фильтр на Polo"), ("out", "Есть Knecht OC90, 320 ₽")]
        assert msgs[1]["author"] == "Светлана"
        assert len(pushes) == 1 and pushes[0]["message"]["data"]["type"] == "chat"
        assert any(m == "imconnector.send.status.delivery" for m, _ in calls)


def test_pushto_recipients():
    from app.push import match_recipients, split_recipients
    assert split_recipients("9497384, +7 978 123-45-67 Заказ 10% готов") == ("9497384,+7978123-45-67", "Заказ 10% готов")
    assert split_recipients("9497384: 2 дня скидка") == ("9497384", "2 дня скидка")
    found, missing = match_recipients("9497384,+79781234567,111", {"9497384": "", "5": "8 (978) 123-45-67"})
    assert found == {"9497384", "5"} and missing == ["111"]


def test_push_notification_payload():
    from app.push import notification_for
    assert notification_for({"type": "promo", "title": "Автодруг92", "body": "Скидка"})["channel_id"] == "promo"
    assert notification_for({"type": "chat", "body": "Да"})["click_action"] == "ru.avtodrug92.OPEN_CHAT"
    assert notification_for({"type": "order_status", "body": "x"}) is None  # у заказов свои кнопки в приложении
