"""Проверка API без настоящего ABCP: запросы уходят в заглушку MockTransport."""
import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient

from app import tokens
from app.abcp import Abcp
from app.config import Settings
from app.main import RateLimiter, create_app

S = Settings(abcp_host="https://abcp.test", admin_login="admin", admin_md5="a" * 32, token_secret=b"s" * 40,
             articles_info_per_day=100, guest_profile_id="156077169")
GOOD_MD5 = hashlib.md5(b"secret").hexdigest()


def fake_abcp(request: httpx.Request) -> httpx.Response:
    p = request.url.path.strip("/")
    q = dict(request.url.params)
    admin = q.get("userlogin") == "admin" and q.get("userpsw") == "a" * 32
    if p == "user/info":
        if q.get("userlogin") == "ivan" and q.get("userpsw") == GOOD_MD5:
            return httpx.Response(200, json={"id": "101", "name": "Иван Петров"})
        return httpx.Response(403, json={"errorCode": 102, "errorMessage": "Wrong name or password!"})
    if p == "user/new":
        f = dict(httpx.QueryParams(request.content.decode()))
        if f.get("mobile") == "79780000000":
            return httpx.Response(200, json={"status": 0, "errorMessage": {"mobile": "Номер уже зарегистрирован"}})
        assert f["office"] == "27993" and f["marketType"] == "1" and "userlogin" not in f
        return httpx.Response(200, json={"status": 1, "userCode": "555"})
    if p == "user/restore":
        f = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(200, json={"status": 2 if f.get("code") else 1, "message": "ok"})
    assert admin, f"админский запрос {p} без админского доступа"
    if p == "cp/users":
        # как у ABCP: объект по индексам
        return httpx.Response(200, json={"0": {"userId": "101", "balance": "0", "debt": "1826.50",
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
    ok = {"name": "Иван", "mobile": "+7 (978) 123-45-67", "password": "secret1", "office": "27993"}
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
