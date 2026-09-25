# Avtodrug API — сервер приложения

Нужен потому, что часть ABCP доступна только API-администратору, а его пароль нельзя класть
в приложение (достаётся из APK за минуты). Здесь же пароль Laximo, push, Telegram-бот и чат с Битрикс24.
Персональные данные клиентов сервер не хранит: имя и телефон берёт из ABCP в момент запроса.

## Запросы

| Запрос | Что делает |
|---|---|
| `POST /v1/session` | Проверяет логин клиента (`user/info`), выдаёт токен на 30 дней. Пароль не хранится |
| `GET /v1/me/finance` | Баланс, долг, кредитный лимит, профиль цен — `cp/users` |
| `GET /v1/orders/{номер}/pay` | Ссылка на оплату — только своего неоплаченного заказа |
| `POST /v1/orders/{номер}/app-note` | Служебная заметка «📱 Оформлен через приложение» (клиент не видит) |
| `GET /v1/topup?amount=` | Ссылка на пополнение баланса |
| `POST /v1/images` | Картинки товаров — `articles/info`, не больше лимита в сутки, кэш |
| `GET /v1/guest/brands`, `/v1/guest/offers` | Поиск без входа: цены гостевого профиля, закупочные цены вырезаются, ★ считается здесь |
| `POST /v1/register`, `/v1/restore` | Регистрация (`cp/user/new`) и восстановление пароля — ABCP пускает их только с IP сервера |
| `POST /v1/laximo/{метод}` | Подбор по авто — прокси к Laximo по белому списку методов |
| `GET /v1/brands/warranty` | Гарантии избранных брендов — `app/data/brand_warranty.json` (правится без выпуска приложения) |
| `POST/GET/DELETE /v1/access-request` | Заявка на включение прав API → менеджерам в Telegram |
| `POST/DELETE /v1/push/token` | Push-токен устройства (RuStore) |
| `POST /v1/admin/push-test` | Тестовый push клиенту (`X-Upload-Token`) |
| `/v1/bitrix/install`, `/placement`, `/event` | Канал «Приложение Автодруг» в открытой линии Битрикс24 |
| `GET /v1/chat/status`, `/messages`, `POST /v1/chat/send` | Свой чат приложения через Битрикс24 (пока выключен, см. ниже) |
| `GET /v1/app/latest`, `GET/PUT /v1/app/apk/{номер}` | Автообновление версии `direct`: CI загружает сборку, приложение скачивает |

## Фоновые задачи

- **Статусы заказов** (`push.OrderWatcher`) — раз в 3 минуты `cp/orders` по дате изменения (время МСК),
  изменения позиций → push. «Готово к выдаче», «Ждёт оплаты», «Задерживается» — свой текст, у «готово» адрес офиса.
  Push статусов — только данные (уведомление рисует приложение: лента, кнопки «Маршрут»/«Оплатить»).
  Рассылки, чат и «Доступ включён» идут с готовым уведомлением — его показывает RuStore, так быстрее.
- **Бот @av92_bot** (long polling, вебхук не нужен) — заявки на доступ с кнопкой «✅ Доступ включён»
  (клиенту уходит push) и команды:

  | Команда | Что делает |
  |---|---|
  | `/push текст` | Уведомление всем клиентам с приложением |
  | `/pushto ID или телефон текст` | Одному или нескольким (через запятую); можно `/pushto 9497384: текст` |
  | `/groups`, `/pushgroup номер текст` | Группы = профили ABCP (Розница, Опт…) и рассылка группе |
  | `/clients` | Кто получает уведомления (ID, имя, телефон — из ABCP) |
  | `/stats` | Клиентов и устройств с push, заявки на доступ |

  Все рассылки — только после кнопки «✅ Отправить». Бот @avtodrug82_bot — другой (site-chat.me), его не трогать.

## Чат через Битрикс24

Сейчас в приложении работает веб-чат открытой линии: имя, телефон, ID клиента в ABCP и версия приложения
уходят оператору в CRM. Свой канал (коннектор `avtodrug_app`: push об ответе менеджера, работает при VPN)
написан, но не подключён: bitrix.freno.ru (MikroTik) не пускает сервер 194.87.208.68. Порядок подключения:
разрешить IP на MikroTik → в Битрикс24 «Переустановить» локальное приложение → включить канал
в настройках открытой линии. Приложение само переключится на свой чат, когда `/v1/chat/status` вернёт `enabled`.

## Безопасность

- Админский доступ ABCP, пароль Laximo, токены бота, RuStore и Битрикс24 — только в `/etc/avtodrug-api.env` (600).
- Каждый запрос — строго по клиенту из токена; оплата и заметка — только к своему заказу.
- Лимиты: вход и ввод SMS-кода — 10 в минуту с IP, регистрация и запрос кода — 5 в час, гостевой поиск — 60 в минуту.
- Закупочные цены (`priceIn`) наружу не отдаются никогда; в журнале только метод, путь и код ответа
  (журнал httpx отключён — в адресах запросов к ABCP админский пароль).
- События Битрикс24 принимаются только с `application_token`, выданным при установке, и только с портала `BITRIX_DOMAIN`.

## Где работает

Тестовый VPS 194.87.208.68 (Нидерланды) — на нём же личный VPN, **перед и после любых изменений проверять
VPN-подписку**. Сервис `avtodrug-api` (uvicorn на 127.0.0.1:8090, код `/opt/avtodrug-api`, данные
`/var/lib/avtodrug-api`), снаружи nginx с HTTPS на порту 8446. Для 152-ФЗ со временем нужен VPS в России.

Переменные окружения — [deploy/avtodrug-api.env.example](deploy/avtodrug-api.env.example).

## Выкладка

```bash
scp app/*.py root@194.87.208.68:/opt/avtodrug-api/app/
ssh root@194.87.208.68 systemctl restart avtodrug-api
```

Первая установка на новый сервер (Ubuntu):

```bash
apt install -y python3-venv nginx
useradd --system --home /opt/avtodrug-api avtodrug
git clone https://github.com/Strije/Abcp.git /tmp/abcp && cp -r /tmp/abcp/server /opt/avtodrug-api
python3 -m venv /opt/avtodrug-api/venv && /opt/avtodrug-api/venv/bin/pip install -r /opt/avtodrug-api/requirements.txt
install -m 600 /opt/avtodrug-api/deploy/avtodrug-api.env.example /etc/avtodrug-api.env   # и заполнить
cp /opt/avtodrug-api/deploy/avtodrug-api.service /etc/systemd/system/ && systemctl enable --now avtodrug-api
```

## Тесты

```bash
pip install -r requirements-dev.txt && pytest -q
```

## Слежение

- `deploy/watchdog.py` + `avtodrug-watchdog.timer` — каждые 5 минут: API, диск, срок HTTPS-сертификата,
  VPN-подписка. В Telegram — только «сломалось» / «починилось». Раз в сутки копия данных в `/var/backups/avtodrug-api` (14 последних).
- `.github/workflows/uptime.yml` — проверка снаружи раз в 15 минут (секреты `TG_BOT_TOKEN`, `TG_CHAT_ID`).
