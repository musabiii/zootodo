# ZooTodo

Todo-менеджер, где задачи делятся по характеру: срочное, важное, неприятное, обычное, на 5 минут и долгосрочное. Оформление переключается между вселенными (зоопарк, космос, RPG, киберпанк, западное побережье, сетка).

## Запуск

Нужен только Python 3.9+, без зависимостей.

```bash
python server.py
```

Откройте http://localhost:8000, зарегистрируйтесь или войдите. Задачи хранятся на сервере в `zootodo.db` (SQLite) отдельно для каждого пользователя.

Если открыть `index.html` просто как файл, приложение работает без входа и хранит задачи в браузере (localStorage). При регистрации задачи с этого устройства переносятся в новый аккаунт.

## Настройки сервера

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `PORT` | `8000` | порт |
| `HOST` | `127.0.0.1` | адрес; `0.0.0.0`, чтобы открыть в локальной сети |
| `ZOOTODO_DB` | `zootodo.db` рядом с `server.py` | путь к базе |
| `COOKIE_SECURE` | — | `1`, если сервер работает за HTTPS |

## Docker

```bash
docker build -t zootodo .
docker run -d -p 8000:8000 -v zootodo-data:/data --name zootodo zootodo
```

База лежит в томе `/data` (`/data/zootodo.db`), так что данные переживают пересборку контейнера.

Готовый образ (amd64 и arm64) собирается GitHub Actions при каждом пуше в `master`:

```bash
docker run -d -p 8000:8000 -v zootodo-data:/data ghcr.io/musabiii/zootodo:latest
```

Теги: `latest` — последняя сборка `master`, `1.2.3` — git-тег `v1.2.3`, `sha-…` — конкретный коммит.

### Coolify

1. Новый ресурс из Git-репозитория, Build Pack — **Dockerfile**. Или ресурс **Docker Image** с готовым `ghcr.io/musabiii/zootodo:latest` — тогда Coolify ничего не собирает сам.
2. Ports Exposes: `8000`.
3. Storages: постоянный том с путём `/data`.
4. Переменные окружения: `COOKIE_SECURE=1`, раз Coolify отдаёт сайт по HTTPS.

## Как устроена авторизация

- Логин и пароль, пароль хранится как хеш `scrypt` с солью.
- После входа сервер выставляет cookie `sid` (HttpOnly, SameSite=Lax) на 30 дней.
- API: `POST /api/register`, `POST /api/login`, `POST /api/logout`, `GET /api/me`, `GET`/`PUT /api/state`.
