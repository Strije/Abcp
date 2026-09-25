# Avtodrug API

Небольшой сервер для приложения «Автодруг». Нужен потому, что часть ABCP доступна только
API-администратору, а его пароль нельзя класть в приложение (достаётся из APK за минуты).

| Запрос | Что делает |
|---|---|
| `POST /v1/session` | Проверяет логин клиента через `user/info` и выдаёт токен на 30 дней. Пароль не хранится. |
| `GET /v1/me/finance` | Баланс, долг, сальдо, кредитный лимит, профиль (уровень цен) — `cp/users` |
| `GET /v1/orders/{номер}/pay` | Ссылка на оплату — только своего и неоплаченного заказа (`cp/order` + `cp/payment/token`) |
| `GET /v1/topup?amount=` | Ссылка на пополнение баланса — `cp/payment/top-balance-link` |
| `POST /v1/images` | Картинки товаров — `articles/info?format=bni`, кэш на сутки |
| `POST /v1/laximo/{метод}` | Подбор по VIN / кузову / госномеру и схемы — прокси к Laximo, пароль Laximo только здесь |
| `GET /v1/app/latest`, `GET/PUT /v1/app/apk/{номер}` | Автообновление: CI загружает сборку main по `APP_UPLOAD_TOKEN`, приложение скачивает |

Безопасность: админский доступ только в `/etc/avtodrug-api.env` (600); каждый запрос — строго по
клиенту из токена; ссылка на оплату — только на свой заказ; не больше 10 попыток входа в минуту с IP;
сервис слушает 127.0.0.1, наружу — Caddy с HTTPS; в логах нет строк запросов и заголовков авторизации.

## Где размещать

VPS **в России** (персональные данные покупателей — 152-ФЗ; при «белых списках» зарубежные адреса
режутся первыми). Хватит самого маленького тарифа: 1 vCPU, 1 ГБ.

## Установка (Ubuntu)

```bash
apt install -y python3-venv caddy
useradd --system --home /opt/avtodrug-api avtodrug
git clone -b cleanup https://github.com/Strije/Abcp.git /tmp/abcp && cp -r /tmp/abcp/server /opt/avtodrug-api
python3 -m venv /opt/avtodrug-api/venv && /opt/avtodrug-api/venv/bin/pip install -r /opt/avtodrug-api/requirements.txt
install -m 600 /opt/avtodrug-api/deploy/avtodrug-api.env.example /etc/avtodrug-api.env   # и заполнить
cp /opt/avtodrug-api/deploy/avtodrug-api.service /etc/systemd/system/ && systemctl enable --now avtodrug-api
cp /opt/avtodrug-api/deploy/Caddyfile /etc/caddy/Caddyfile && systemctl reload caddy
curl https://api.avtodrug92.ru/health
```

## Тесты

```bash
pip install -r requirements-dev.txt && pytest -q
```
