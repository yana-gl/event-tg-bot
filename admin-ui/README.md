# Админка tg-event

Vue 3, Vite, Pinia и Vuestic UI. Оболочка, тема и базовые стили адаптированы из [Vuestic Admin](https://github.com/epicmaxco/vuestic-admin): AppLayout, AppNavbar, AppSidebar и настройки src/services/vuestic-ui. Лицензия исходного шаблона находится в public/VUESTIC-LICENSE и включается в production-сборку.

Используются существующие API Python-сервера и авторизация проекта. Демо-страницы, аналитика и внешняя авторизация шаблона не подключены. Шрифты Inter (включая кириллицу) и Material Icons включены в сборку и не требуют CDN.

## Разработка

Из каталога admin-ui:

```bash
npm ci
npm run dev
```

Vite проксирует /api на http://127.0.0.1:8080. Python API запускается из корня проекта командой python -m tg_event.cli serve --dev.

## Production

```bash
npm run build
```

Каталог dist отдаётся командой python -m tg_event.cli serve из корня проекта. dist не коммитится.
