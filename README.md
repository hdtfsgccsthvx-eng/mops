# WALKIE Telegram Mini App v23.1

Полная сборка из переданного владельцем архива WAVKIE v23.1 перенесена в `WALKIE_v23_1_DEPLOY.zip`.

## Railway
Один сервис из `Dockerfile`: Python веб-сервер (`app_v15.py`), Telegram bot webhook, API и Mini App. Переменные окружения: `BOT_TOKEN`, `MINI_APP_URL`, `TEST_MODE=false`, `DATA_DIR=/data`, `DB_PATH=/data/walkie.sqlite3`, `TELEGRAM_WEBHOOK_SECRET`. Постоянный volume подключён на `/data`.

## API
- `GET /health`: health check
- `POST /telegram/webhook`: Telegram API webhook
- `GET /api/catalog`: услуги
- `/integrations/walkie/site-event`: события с WordPress, включается отдельно через `WALKIE_SITE_WEBHOOK_SECRET`

Исходники вместе со статикой содержатся в ZIP; распаковка производится при сборке Docker-образа. Корневой прототип старого бота из первоначального архива не запускается.

Настройка WebApp и webhook происходит автоматически при запуске, если заданы BOT_TOKEN и MINI_APP_URL. Не сохраняйте ключи API в GitHub.
