# Лабораторная работа №2. Docker Compose

Проект продолжает `dataset-profiler` из лабораторной 1. Сервис по-прежнему читает CSV из каталога `/data`, а посчитанный профиль ещё и записывает в PostgreSQL. Схема базы появляется не в приложении, а в одноразовом init-контейнере.

Команды ниже запускаются из каталога `lab2/`.

## Состав

- `Dockerfile` — хороший образ из лабораторной 1 плюс `psycopg2-binary`. Compose собирает его сам и кладёт в локальное имя `dataset-profiler:lab2`.
- `docker-compose.yml` — три сервиса, одна сеть, значения окружения только ссылками на `.env`.
- `.env` — пользователь, пароль и имя базы, хост, порт публикации и каталог CSV.
- `init/init.sh`, `init/001_schema.sql` — одноразовое создание таблицы `profiles`.
- `app/main.py` — HTTP API.
- `data/` — CSV, которые монтируются в приложение и в образ не копируются.

Эндпоинты:

- `GET /health` — процесс жив и PostgreSQL отвечает на `SELECT 1`. Токен не нужен, на этот путь смотрит healthcheck.
- `GET /datasets` — имена `*.csv` в `/data`.
- `GET /datasets/{name}/profile` — статистики колонок. Результат сразу пишется в таблицу `profiles`.
- `GET /profiles` — последние сохранённые профили.
- `GET /docs` — схема API.

`PROFILER_TOKEN` в `.env` не задан, поэтому для локального запуска заголовок авторизации не нужен. Если переменную добавить в `.env`, `/datasets`, `/profile` и `/profiles` начнут требовать `Authorization: Bearer <token>`.

## Сервисы

Все три подключены к одной сети `profiler`. Её имя на хосте фиксировано: `dataset-profiler-net`. Имена контейнеров тоже фиксированы, без префикса проекта.

### `postgres` — база

Образ `postgres:16.6`, контейнер `dataset-profiler-db`. Пользователь, пароль и имя базы приходят из `.env` через `env_file`, в compose-файле их нет. Данные лежат в именованном томе `dataset-profiler-db-data`, смонтированном в `/var/lib/postgresql/data`.

Healthcheck вызывает `pg_isready`. Имя пользователя и базы подставляются из `.env` в момент чтения compose-файла: в репозитории это `${POSTGRES_USER}` и `${POSTGRES_DB}`, не сами значения.

### `init` — одноразовая схема

Тот же образ `postgres:16.6`, контейнер `dataset-profiler-init`. Своего сервера он не поднимает: `entrypoint` заменён на `/bin/sh /init.sh`. Скрипт дожидается, пока база примет запрос, и выполняет `001_schema.sql`. Таблица создаётся через `CREATE TABLE IF NOT EXISTS`, поэтому повторный `up` не ломает уже накопленные профили.

`restart: "no"` оставляет его одноразовым. `depends_on` с `condition: service_healthy` не пускает скрипт раньше, чем `pg_isready` у postgres станет успешным. После применения схемы процесс завершается с кодом 0.

### `profiler` — приложение

Собирается из `Dockerfile` рядом с compose-файлом. Ключ `image: dataset-profiler:lab2` даёт собранному образу постоянное имя. Контейнер называется `dataset-profiler-app`.

`entrypoint` и `command` повторяют команду из Dockerfile: uvicorn слушает `0.0.0.0:8000`. Наружу порт публикуется как `${APP_PORT}:8000`, значение `APP_PORT` берётся из `.env`. Каталог `./data` смонтирован в `/data` только на чтение.

Сервис ждёт здоровую базу и завершившийся init (`condition: service_completed_successfully`). Свой healthcheck ходит на `http://127.0.0.1:8000/health` штатным `python`, без curl: в slim-образе curl нет.

## Запуск и проверка

```bash
docker compose up --build -d
docker compose ps
```

Дождаться, пока `dataset-profiler-init` станет `Exited (0)` и у `dataset-profiler-app` появится `healthy`.

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8000/datasets
curl -sS http://127.0.0.1:8000/datasets/experiment_runs.csv/profile
curl -sS http://127.0.0.1:8000/profiles
```

Повторный запрос профиля добавляет ещё одну строку в `/profiles`. Лог init:

```bash
docker compose logs init
```

Остановка с сохранением тома базы:

```bash
docker compose down
```

Вместе с данными базы:

```bash
docker compose down -v
```

## Можно ли ограничить память и CPU

Да. В Docker Compose v2, который здесь стоит как `docker compose` / `docker-compose` v2.15.1, лимит задаётся блоком `deploy.resources` и применяется обычным `docker compose up`, без Swarm:

```yaml
services:
  profiler:
    deploy:
      resources:
        limits:
          cpus: "0.50"
          memory: 512M
        reservations:
          cpus: "0.25"
          memory: 256M
```

Те же ограничения принимают старые ключи прямо на сервисе: `mem_limit: 512m` и `cpus: 0.50`. Compose v2 приводит их к тем же лимитам контейнера.

Проверка после запуска:

```bash
docker inspect dataset-profiler-app --format 'memory={{.HostConfig.Memory}} cpus={{.HostConfig.NanoCpus}}'
```

`Memory` будет в байтах, `NanoCpus` — в нано-CPU (`0.50` превращается в `500000000`).

Оговорка про старый клиент: у `docker-compose` v1 и файла версии 3 блок `deploy` применялся только в Swarm и при обычном `up` игнорировался. На v2 это уже не так. В этом проекте лимиты в файл не поставлены, чтобы учебный postgres и pandas не упирались в слишком тесный потолок. Добавляются они фрагментом выше.

## Как запустить только один сервис

Имя сервиса передаётся в `up`:

```bash
docker compose up -d postgres
```

У `postgres` нет `depends_on`, поэтому поднимется только база.

У `profiler` зависимости есть. Команда `docker compose up profiler` поднимет и их: init и postgres. Чтобы не трогать остальные сервисы, нужен `--no-deps`:

```bash
docker compose up -d --no-deps profiler
```

Так приложение стартует одно. Имеет смысл, только если база уже запущена и таблица `profiles` уже создана. Иначе `/health` останется неуспешным.

Тот же флаг есть у одноразового запуска:

```bash
docker compose run --rm --no-deps init
```

Без `--no-deps` `run` тоже сначала поднимет зависимости init, то есть postgres.

## Соответствие заданию

- Три сервиса: одноразовый `init`, приложение `profiler` и база `postgres`.
- Образ приложения собирается из `Dockerfile` в этом каталоге и получает имя `dataset-profiler:lab2`.
- У контейнеров жёсткие имена: `dataset-profiler-db`, `dataset-profiler-init`, `dataset-profiler-app`.
- `depends_on` есть у `init` и у `profiler`.
- Тома есть у базы и у приложения.
- Порт приложения опубликован наружу.
- У `init` задан `entrypoint`, у `profiler` заданы `entrypoint` и `command`.
- Healthcheck есть у `postgres` и у `profiler`.
- Значения окружения лежат в `.env`. В compose нет пароля и нет блока `environment`.
- Сеть `profiler` указана явно и подключена ко всем сервисам.
