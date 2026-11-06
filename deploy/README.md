# Первый деплой на Ubuntu

Приложение установлено в /opt/tg-event. Основной адрес админки — https://event-admin.localplayer.dev. Caddy принимает HTTPS и передаёт запросы Python-серверу на 127.0.0.1:8080. Службы приложения работают от отдельного системного пользователя tg-event. Код принадлежит root; изменяемые данные находятся в /var/lib/tg-event.

## Файлы и службы

| Что | Где |
| --- | --- |
| Код, виртуальное окружение и собранный фронтенд | /opt/tg-event |
| Конфигурация и секреты | /etc/tg-event.env |
| База SQLite | /var/lib/tg-event/tg_event.sqlite3 |
| Экспорты, прогресс и Telegram-сессии | /var/lib/tg-event |
| Начальные данные входа в админку | /var/lib/tg-event/admin-login.txt |
| Последние семь ежедневных резервных копий SQLite | /var/backups/tg-event |
| Админка | tg-event-admin.service |
| Бот и сборщик | tg-event-watch.service |
| Расписание резервных копий | tg-event-backup.timer |
| HTTPS и продление сертификата | caddy.service |
| Конфигурация HTTPS | /etc/caddy/Caddyfile |

install.sh запускается от root после загрузки кода, tests, requirements.txt, source_rules.json, deploy и admin-ui/dist в /opt/tg-event. Требуются python3-venv и доступ к PyPI. Секретные локальные файлы, корневой .env.example, .git, node_modules и Telegram-сессии в пакет установки не включаются.

```bash
sh /opt/tg-event/deploy/install.sh
```

Установщик создаёт новые случайные ADMIN_PASSWORD и ADMIN_SECRET и сохраняет существующий /etc/tg-event.env при повторном запуске. Админка и таймер резервного копирования включаются сразу. Watcher устанавливается, но остаётся выключенным до заполнения реквизитов и авторизации Telegram.

## Заполнение реквизитов

В SSH-терминале сервера:

```bash
nano /etc/tg-event.env
```

Заменить TELEGRAM_API_ID=1, TELEGRAM_API_HASH=NOT_CONFIGURED, TELEGRAM_PHONE=NOT_CONFIGURED, OPENROUTER_API_KEY=NOT_CONFIGURED и заполнить BOT_TOKEN. ADMIN_PASSWORD и ADMIN_SECRET уже созданы; пути настроены для сервера.

В nano сохранить через Ctrl+O, Enter и выйти через Ctrl+X. Пароли, коды авторизации и токены не отправлять в чат, не коммитить. Если использовались настоящие реквизиты из корневого .env.example, сначала отозвать их у соответствующих сервисов и выпустить новые: удаление файла не убирает значения из истории Git.

Проверить заполнение без вывода значений:

```bash
cd /opt/tg-event
runuser -u tg-event -- .venv/bin/python deploy/check_config.py
```

Авторизовать сборщик в интерактивном терминале; код и пароль Telegram вводятся прямо в терминал:

```bash
runuser -u tg-event -- .venv/bin/python deploy/authorize.py
```

Остановить другой экземпляр watcher с этим же ботом/сессией, если он работает на Mac. После авторизации включить серверный:

```bash
systemctl enable --now tg-event-watch.service
systemctl restart tg-event-admin.service
systemctl status tg-event-watch.service --no-pager
```

Если перенесены существующие данные, сохранить профиль watch_progress перед запуском: он привязан к полному пути --state. Сервер использует /var/lib/tg-event/state/channels.json. Перенос старой базы и выбор профиля требуют отдельной проверки; установщик их автоматически не изменяет.

## Постоянный HTTPS-доступ

Открыть https://event-admin.localplayer.dev с любого устройства. SSH-туннель для этого адреса не нужен. Логин и пароль прежние; на Mac они сохранены в data/deploy/admin-login.txt. DNS-запись A домена направлена на 193.135.137.137, проксирование Cloudflare выключено.

Caddy 2.11.9 установлен из официального подписанного репозитория Caddy и включён с автозапуском. Сертификат Let's Encrypt выпускается и продлевается автоматически; HTTP перенаправляется на HTTPS. Внешние порты — 80 и 443, Python слушает только loopback. Файл deploy/Caddyfile — копия рабочей конфигурации.

Вход ограничен пятью попытками на адрес клиента за 60 секунд; успешный вход сбрасывает счётчик. Caddy сам задаёт X-Real-IP, приложение доверяет этому заголовку только при подключении с loopback. Размер запроса ограничен 1 МБ; API возвращает Cache-Control: no-store. Превышение лимита входа возвращает 429, размера запроса — 413.

После изменения конфигурации:

```bash
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl reload caddy.service
```

install.sh не устанавливает Caddy и не меняет HTTPS-конфигурацию. Для нового сервера сначала настроить DNS, установить Caddy по официальной инструкции https://caddyserver.com/docs/install и установить deploy/Caddyfile в /etc/caddy/Caddyfile. Каталог данных Caddy с сертификатами сохранять при обновлении приложения.

## Резервный доступ через SSH-туннель с Mac

В новом локальном терминале Mac:

```bash
ssh -i ~/.ssh/tg_event_deploy -o IdentitiesOnly=yes -N -L 127.0.0.1:18080:127.0.0.1:8080 root@193.135.137.137
```

Открыть http://127.0.0.1:18080. Терминал с туннелем должен оставаться открытым. Начальные данные входа можно посмотреть в своём SSH-терминале командой cat /var/lib/tg-event/admin-login.txt.

Порт 8080 остаётся закрытым для прямого доступа из интернета; его не нужно открывать для HTTPS.

## Вход на сервер по SSH-ключу

На текущем VPS разрешён только вход по публичному ключу. Политика из ssh-key-only.conf установлена в /etc/ssh/sshd_config.d/00-tg-event-key-only.conf: парольная и keyboard-interactive аутентификация отключены, root допускается по ключу. Пароль учётной записи root не удалялся.

В терминале Mac:

```bash
ssh -i ~/.ssh/tg_event_deploy -o IdentitiesOnly=yes root@193.135.137.137
```

Приватный ключ ~/.ssh/tg_event_deploy нужен для входа; его резервную копию следует хранить в защищённом месте. После изменения проверены sshd -t, эффективные настройки, новое подключение по ключу и отказ подключения без ключа. Изменение применено через reload без завершения действующих SSH-сеансов. install.sh не меняет настройки SSH: на другом сервере сначала нужно настроить и проверить собственный ключ.

## Проверки и резервные копии

```bash
systemctl status tg-event-admin.service --no-pager
systemctl status caddy.service --no-pager
journalctl -u tg-event-admin.service -n 30 --no-pager
journalctl -u tg-event-watch.service -n 50 --no-pager
systemctl list-timers tg-event-backup.timer
systemctl start tg-event-backup.service
```

backup.py использует SQLite Backup API, сжимает копию и оставляет семь последних собственных архивов. Прогресс и настройки каналов входят в базу. Telegram-сессии и секреты в этот архив не включены. Резервные копии на том же сервере не защищают от потери сервера: для постоянной эксплуатации их нужно копировать в отдельное хранилище. Логи systemd ограничены 64 МБ на диске и 32 МБ в памяти.

## Обновление

Собрать фронтенд на Mac командой npm ci && npm run build в admin-ui. Проверить тесты. Перед заменой кода сделать резервную копию и остановить службы приложения; загрузить новую версию кода и dist. Не заменять /etc/tg-event.env и /var/lib/tg-event. Установить зависимости из requirements.txt и перезапустить службы. Первое размещение не настраивает автоматический деплой или синхронизацию Git.
