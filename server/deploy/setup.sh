#!/usr/bin/env bash
# Установка сервера приложения Автодруг на чистый VPS (Ubuntu/Debian) и переезд со старого.
# Запускать от root на НОВОМ сервере:
#
#   curl -fsSLo setup.sh https://raw.githubusercontent.com/Strije/Abcp/main/server/deploy/setup.sh
#   bash setup.sh install [адрес]             # 1) поставить всё, настройки со старого, HTTPS — сервис ещё спит
#                                             #    (без адреса — технический адрес сервера из DNS)
#   bash setup.sh move                        # 2) переезд: старый сервис стоп, данные сюда, запуск здесь,
#                                             #    старый адрес проксирует сюда (для старых версий приложения)
#   bash setup.sh update                      # потом: выложить свежий код из git и перезапустить
#
# Пароль root старого сервера спросят один раз (ssh), на старом трогаем только наш сервис и его nginx —
# VPN-подписку проверяем до и после.
set -euo pipefail

OLD="${OLD:-194.87.208.68}"                  # старый сервер
REPO="${REPO:-https://github.com/Strije/Abcp.git}"
BRANCH="${BRANCH:-main}"
DIR=/opt/avtodrug-api
DATA=/var/lib/avtodrug-api
ENVF=/etc/avtodrug-api.env
SITE=/etc/nginx/sites-available/avtodrug-api
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ControlMaster=auto -o "ControlPath=/root/.ssh/avtodrug-%r@%h" -o ControlPersist=15m)

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok() { printf '   \033[32m✔ %s\033[0m\n' "$*"; }
bad() { printf '   \033[31m✘ %s\033[0m\n' "$*"; }
die() { bad "$*"; exit 1; }
old() { ssh -n "${SSH_OPTS[@]}" "root@$OLD" "$@"; }
env_get() { sed -n "s/^$1=//p" "$ENVF" | tail -1; }
env_set() {  # env_set KEY VALUE — заменить строку или дописать
    if grep -q "^$1=" "$ENVF"; then sed -i "s|^$1=.*|$1=$2|" "$ENVF"; else echo "$1=$2" >> "$ENVF"; fi
}
reach() {  # reach URL — отвечает ли сайт хоть как-то (любой HTTP-код)
    local code
    code=$(curl -sS -o /dev/null -m 15 -w '%{http_code}' "$1" 2>/dev/null) || code=000
    [ "$code" != "000" ]
}
check_vpn() {
    local url
    url=$(env_get WATCH_SUBSCRIPTION_URL)
    if [ -z "$url" ]; then bad "VPN-подписка: адреса нет в $ENVF (WATCH_SUBSCRIPTION_URL) — проверьте вручную"; return; fi
    if [ "$(curl -sS -o /dev/null -m 20 -w '%{http_code}' "$url" 2>/dev/null)" = 200 ]; then ok "VPN-подписка отвечает ($1)"
    elif [ "$(old "curl -sS -o /dev/null -m 20 -w '%{http_code}' '$url'" 2>/dev/null)" = 200 ]; then
        ok "VPN-подписка отвечает ($1; отсюда не открывается, со старого сервера — да)"
    else bad "VPN-подписка НЕ отвечает ($1)"; fi
}

deploy_code() {
    local src
    src=$(mktemp -d)
    git clone -q --depth 1 -b "$BRANCH" "$REPO" "$src"
    mkdir -p "$DIR"
    rsync -a --delete --exclude venv --exclude tests --exclude __pycache__ "$src/server/" "$DIR/"
    rm -rf "$src"
    [ -x "$DIR/venv/bin/python" ] || python3 -m venv "$DIR/venv"
    "$DIR/venv/bin/pip" install -q --upgrade pip
    "$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"
    chown -R root:root "$DIR"
    cp "$DIR/deploy/avtodrug-api.service" "$DIR/deploy/avtodrug-watchdog.service" "$DIR/deploy/avtodrug-watchdog.timer" /etc/systemd/system/
    systemctl daemon-reload
    ok "код из $BRANCH — в $DIR"
}

cmd_install() {
    local domain="${1:-}" ip
    [ "$(id -u)" = 0 ] || die "Нужен root"

    say "Пакеты"
    export DEBIAN_FRONTEND=noninteractive
    # На свежем сервере первые минуты работает автообновление Ubuntu — ждём его, а не падаем
    apt-get -o DPkg::Lock::Timeout=900 update -q
    apt-get -o DPkg::Lock::Timeout=900 install -y -q python3-venv git rsync curl nginx certbot python3-certbot-nginx bind9-dnsutils >/dev/null
    ok "python3, nginx, certbot"

    # Адрес — только из настоящего DNS (в /etc/hosts есть имя вида msk-1-vm-…, сертификат на него не дадут)
    ip=$(curl -4 -sS -m 10 https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
    if [ -z "$domain" ]; then  # свой домен не задан — технический адрес сервера (обратная запись DNS)
        domain=$(dig +short -x "$ip" | sed 's/\.$//' | grep '\.' | head -1 || true)
        [ -n "$domain" ] || die "У $ip нет адреса в DNS. Укажите его: bash setup.sh install <адрес>"
        ok "адрес сервера: $domain"
    fi
    if ! dig +short A "$domain" | grep -qx "$ip"; then
        die "$domain пока не ведёт на этот сервер ($ip). Добавьте в DNS запись A: $domain → $ip, подождите 5–30 минут и запустите снова"
    fi
    id avtodrug >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin avtodrug
    ok "пользователь avtodrug"

    say "Код"
    deploy_code

    say "Настройки"
    if [ -f "$ENVF" ]; then ok "$ENVF уже есть — не трогаю"
    else
        echo "   Копирую $ENVF со старого сервера $OLD (спросит пароль root старого сервера)"
        mkdir -p /root/.ssh && chmod 700 /root/.ssh
        scp -q "${SSH_OPTS[@]}" "root@$OLD:$ENVF" "$ENVF"
        ok "скопированы (TOKEN_SECRET тот же — клиентов не выкинет из приложения)"
    fi
    chown root:root "$ENVF"; chmod 600 "$ENVF"
    env_set PUBLIC_URL "https://$domain"

    say "HTTPS для $domain"
    [ -f "$SITE" ] || sed "s/__DOMAIN__/$domain/" "$DIR/deploy/nginx-avtodrug-api.conf" > "$SITE"
    ln -sf "$SITE" /etc/nginx/sites-enabled/avtodrug-api
    nginx -t -q && systemctl reload nginx
    if [ -d "/etc/letsencrypt/live/$domain" ] && grep -q "listen 443" "$SITE"; then ok "сертификат уже есть"
    else
        certbot --nginx -d "$domain" --non-interactive --agree-tos --register-unsafely-without-email --redirect -q
        ok "сертификат получен, продлевается сам (certbot.timer)"
    fi

    say "Доступ отсюда к внешним сервисам"
    for u in "$(env_get ABCP_HOST)" https://ws.laximo.ru https://vkpns.rustore.ru https://bitrix.freno.ru https://api.telegram.org; do
        if reach "$u"; then ok "$u"; else bad "$u — не открывается"; fi
    done
    reach https://api.telegram.org || echo "   Telegram недоступен — при переезде (move) пустим его через старый сервер"

    say "Готово. Сервис пока не запущен — он работает на старом сервере."
    echo "   До переезда: попросить ABCP разрешить регистрацию/восстановление с IP $ip"
    echo "   (и API-администратору, если в ABCP стоит ограничение по IP), затем: bash setup.sh move"
}

cmd_move() {
    local domain tg="" health
    [ -f "$ENVF" ] && [ -f "$SITE" ] || die "Сначала: bash setup.sh install <адрес>"
    domain=$(env_get PUBLIC_URL | sed 's|^https://||; s|[:/].*||')
    mkdir -p /root/.ssh && chmod 700 /root/.ssh
    trap 'bad "Сбой на строке $LINENO — старый сервис остановлен, смотрите «Откат» ниже"; rollback_hint' ERR

    say "До переезда"
    check_vpn "до"
    old true || die "нет связи со старым сервером $OLD"
    ok "старый сервер $OLD на связи"

    say "Старый сервис — стоп"
    old "systemctl disable --now avtodrug-api avtodrug-watchdog.timer" 2>/dev/null || true
    ok "avtodrug-api и сторож на $OLD остановлены (VPN не трогаем)"

    say "Данные (заявки, push-токены, статусы заказов, APK)"
    install -d -m 700 -o avtodrug -g avtodrug "$DATA"
    rsync -a -e "ssh ${SSH_OPTS[*]}" "root@$OLD:$DATA/" "$DATA/"
    chown -R avtodrug:avtodrug "$DATA"
    ok "перенесены"

    if ! reach https://api.telegram.org; then
        say "Telegram — через старый сервер"
        tg="tg-$(openssl rand -hex 12)"
    fi

    say "Старый адрес → сюда (для старых версий приложения)"
    # SSH склеивает хвостовые аргументы команды в одну строку для удалённой оболочки;
    # пустой $tg при этом бесследно исчезает при разбиении на слова, и sys.argv[2] пропадает.
    # Поэтому передаём нейтральную заглушку вместо пустой строки.
    out=$(ssh "${SSH_OPTS[@]}" "root@$OLD" python3 - "$domain" "${tg:-__none__}" 2>&1 <<'PY'
import pathlib, re, shutil, subprocess, sys, time

domain, tg_raw = sys.argv[1], sys.argv[2]
tg = "" if tg_raw == "__none__" else tg_raw
files = sorted({p.resolve() for p in pathlib.Path("/etc/nginx").rglob("*")
         if p.is_file() and "127.0.0.1:8090" in p.read_text(errors="ignore")})
if not files:
    sys.exit("в /etc/nginx нет proxy_pass на 127.0.0.1:8090")
changed, tg_done = {}, False
for f in files:  # сначала всё посчитать, записывать — только когда ясно, что получилось
    text = f.read_text()
    extra = "" if "X-Real-IP" in text else " proxy_set_header X-Real-IP $remote_addr;"
    new = re.sub(r"proxy_pass\s+http://127\.0\.0\.1:8090([^;]*);",
                 lambda m: f"proxy_pass https://{domain}{m.group(1)}; proxy_ssl_server_name on;{extra}", text)
    locs = [m.start() for m in re.finditer(r"\n[ \t]*location\b", new[:new.index(f"https://{domain}")])]
    if tg and not tg_done and locs:  # Telegram для нового сервера: свой закрытый путь → api.telegram.org
        at = locs[-1]
        new = (new[:at] + f"\n    location /{tg}/ {{ proxy_pass https://api.telegram.org/; proxy_ssl_server_name on;"
               f" proxy_set_header Host api.telegram.org; proxy_read_timeout 90s; }}" + new[at:])
        tg_done = True
    changed[f] = new
backup = pathlib.Path(f"/root/nginx-before-move-{time.strftime('%Y%m%d-%H%M%S')}")
backup.mkdir()
for f, new in changed.items():
    shutil.copy2(f, backup / f.name)
    f.write_text(new)
if subprocess.run(["nginx", "-t", "-q"]).returncode != 0:
    for f in files:
        shutil.copy2(backup / f.name, f)
    sys.exit("nginx -t не прошёл — вернул как было")
subprocess.run(["systemctl", "reload", "nginx"], check=True)
print(f"NGINX_OK {backup}")
if tg_done:
    print("TG_OK")
PY
) || true
    if grep -q '^NGINX_OK' <<<"$out"; then ok "nginx на старом сервере: прокси сюда, прежние настройки — $(sed -n 's/^NGINX_OK //p' <<<"$out")"
    else bad "старый адрес не переключён ($out) — старые версии приложения не работают, пока не обновятся"; fi
    if [ -n "$tg" ] && ! grep -q '^TG_OK' <<<"$out"; then bad "Telegram через старый сервер не настроен — бот молчит"; tg=""; fi
    if [ -n "$tg" ]; then
        env_set TELEGRAM_API "https://9077635-oy742028.twc1.net:8446/$tg"
        ok "TELEGRAM_API — через старый сервер"
    fi

    say "Запуск здесь"
    systemctl enable --now avtodrug-api avtodrug-watchdog.timer
    sleep 4
    health=$(curl -sS -m 15 "https://$domain/health" 2>/dev/null || true)
    if [ -n "$health" ]; then ok "https://$domain/health → $health"; else bad "https://$domain/health не отвечает: journalctl -u avtodrug-api -n 50"; fi
    health=$(curl -sS -m 15 "https://9077635-oy742028.twc1.net:8446/health" 2>/dev/null || true)
    if [ -n "$health" ]; then ok "старый адрес → $health"; else bad "старый адрес не отвечает"; fi

    say "После переезда"
    check_vpn "после"
    trap - ERR
    rollback_hint
}

rollback_hint() {
    echo
    echo "   Откат: на $OLD — вернуть nginx из /root/nginx-before-move-*, systemctl reload nginx,"
    echo "   systemctl enable --now avtodrug-api avtodrug-watchdog.timer; здесь — systemctl disable --now avtodrug-api"
}

cmd_update() {
    [ -f "$ENVF" ] || die "Сначала: bash setup.sh install <адрес>"
    deploy_code
    systemctl restart avtodrug-api
    sleep 3
    curl -sS -m 10 http://127.0.0.1:8090/health && echo
}

case "${1:-}" in
    install) shift; cmd_install "$@" ;;
    move) cmd_move ;;
    update) cmd_update ;;
    *) sed -n '2,13p' "$0"; exit 1 ;;
esac
