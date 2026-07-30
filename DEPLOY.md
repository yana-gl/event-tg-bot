# Деплой на VPS

Памятка по развороту `tg-event` на сервере для постоянной работы `watch`.

## Выбор сервера

Минимальные требования: 1 vCPU, 512MB–1GB RAM, 10–15GB SSD, Ubuntu 22.04 или 24.04.

Дешёвые варианты (~150–300₽/мес, оплата картой РФ):

- **Timeweb Cloud** — тариф `Spring` от 179₽/мес. Рекомендуется.
- **RuVDS** — от ~250₽/мес.
- **Aeza, Beget, Ihor** — аналогичные планы.

Зарубежные (нужна иностранная карта):

- **Hetzner Cloud** — CX11/CAX11 от ~€4/мес.
- **Vultr, DigitalOcean** — $5–6/мес.

## Подготовка сервера

Подключиться по SSH:

```bash
ssh root@<SERVER_IP>
```

Обновить систему и поставить Python:

```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip git sqlite3
```

Создать пользователя для сервиса (не запускать от root):

```bash
adduser tgbot
usermod -aG sudo tgbot
su - tgbot
```

## Получение кода и окружения

Склонировать репозиторий:

```bash
cd ~
git clone https://github.com/yana-gl/event-tg-bot.git tg-event
cd tg-event
```

Виртуальное окружение и зависимости:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Скопировать `.env.example` в `.env` и заполнить ключи:

```bash
cp .env.example .env
nano .env
```

Обязательные поля:

```env
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_PHONE=
OPENROUTER_API_KEY=
CHANNELS=
CITY=
```

## Первый запуск (интерактивный логин Telegram)

Telethon при первом запуске запросит код из Telegram и возможно пароль 2FA. Сессия сохранится в `tg_event.session` и дальше будет работать без интерактива.

```bash
source .venv/bin/activate
python -m tg_event.cli collect
```

После ввода кода коллекция завершится и в `data/` появятся файлы. Сессия готова.

## Проверка перед запуском сервиса

Один цикл `watch`, чтобы убедиться, что парсинг работает:

```bash
python -m tg_event.cli watch --once --max-posts-per-channel 3
```

Проверить БД:

```bash
sqlite3 data/tg_event.sqlite3
.tables
SELECT id, title, date, status FROM events ORDER BY id DESC LIMIT 10;
.quit
```

## systemd unit для постоянной работы

Создать файл сервиса (вернуться под root или через sudo):

```bash
exit
sudo nano /etc/systemd/system/tg-event.service
```

Содержимое (поправить пути под своего пользователя и папку):

```ini
[Unit]
Description=tg-event watcher
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tgbot
WorkingDirectory=/home/tgbot/tg-event
ExecStart=/home/tgbot/tg-event/.venv/bin/python -m tg_event.cli watch --interval 1800
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Запуск и автозагрузка:

```bash
sudo systemctl daemon-reload
sudo systemctl enable tg-event
sudo systemctl start tg-event
```

Проверка статуса:

```bash
sudo systemctl status tg-event
```

Логи:

```bash
sudo journalctl -u tg-event -f
sudo journalctl -u tg-event --since today
```

Остановить / перезапустить:

```bash
sudo systemctl stop tg-event
sudo systemctl restart tg-event
```

## Полезные команды на сервере

База:

```bash
sqlite3 ~/tg-event/data/tg_event.sqlite3
.tables
SELECT id, title, date, time, place, category, status FROM events ORDER BY id DESC LIMIT 20;
SELECT source_id, message_id, url FROM raw_posts ORDER BY id DESC LIMIT 10;
.quit
```

Обновить код из репозитория:

```bash
cd ~/tg-event
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart tg-event
```

## Что важно помнить

- `tg_event.session` привязан к серверу. Скопировать с мака не выйдет — Telegram отвяжет. Логин проводим на сервере один раз.
- `.env` не коммитить. На сервер заносим вручную.
- БД `data/tg_event.sqlite3` живёт на сервере. Для бэкапа: `sqlite3 data/tg_event.sqlite3 ".backup '/tmp/backup.sqlite3'"`.
- `--since` по умолчанию берёт сегодняшнюю дату. Чтобы обработать посты с прошлых дат, запустить разово: `python -m tg_event.cli watch --once --since 2026-07-01`.
- Логи Telethon (сообщения вида `Server resent the older message...`) — норма, не ошибка.