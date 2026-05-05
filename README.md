# Knowledge Base

Локальная семантическая система для Obsidian-vault'ов и PDF-библиотеки.
Гибридный RAG (dense + sparse + graph walk) с KGE-предсказаниями связей, Wikidata-обогащением и eval-фреймворком.

```
Open WebUI / curl ──► FastAPI :8000 ──► Hybrid RAG ──► Qdrant + Oxigraph + Ollama
                                                     ◄── Watchdog (vault + books)
```

---

## Требования

| Компонент                                              | Минимум                       |
| ------------------------------------------------------ | ----------------------------- |
| Python                                                 | 3.11+                         |
| GPU VRAM                                               | 7 GB (NVIDIA, с `nvidia-smi`) |
| [uv](https://docs.astral.sh/uv/)                       | последний                     |
| [Ollama](https://ollama.com)                           | установлен и запущен          |
| [Open WebUI](https://github.com/open-webui/open-webui) | устанавливается скриптом      |


---

## Установка

```bash
./scripts/install.sh          # review mode: покажет команды без запуска служб
./scripts/install.sh --auto   # полная установка + systemd enable --now
```

Скрипт:
1. Определяет GPU-профиль (`laptop-4070-8gb` / `desktop-mid-12-16gb` / `desktop-large-24gb-plus`)
2. Создаёт `.venv` через `uv sync`
3. Создаёт директории `data/`
4. Копирует `.env.example` → `.env` с нужным профилем
5. Скачивает модели (Qwen3-Embedding, Qwen3-Reranker) → `data/models/`
6. Скачивает Ollama-модель (`qwen3:8b` / `qwen3:14b`)
7. Устанавливает Open WebUI через `uv tool`
8. Клонирует SearXNG → `vendor/searxng/`
9. Генерирует systemd-юниты → `infra/systemd/generated/`

### Принудительный профиль

```bash
./scripts/install.sh --profile=desktop-mid-12-16gb
```

Доступные профили: `laptop-4070-8gb`, `desktop-mid-12-16gb`, `desktop-large-24gb-plus`. Тестировалось на 4070 только.

---

## Добавление материалов

### Obsidian-vault'ы

Отредактируй `infra/config.yaml`:

```yaml
sources:
  vaults:
    - id: personal          # slug, уникальный
      label: "Личный"
      path: /home/user/Sync/vault-personal
      obsidian_uri_name: personal
      # sensitive_entities_file: ./privacy/personal-sensitive.txt  # опционально
    - id: work
      label: "Рабочий"
      path: /home/user/vault-work
      obsidian_uri_name: work
```

Или через переменные окружения (`.env`):

```
KB_SOURCES__VAULTS__0__ID=personal
KB_SOURCES__VAULTS__0__PATH=/home/user/Sync/vault-personal
KB_SOURCES__VAULTS__0__OBSIDIAN_URI_NAME=personal
KB_SOURCES__VAULTS__1__ID=work
KB_SOURCES__VAULTS__1__PATH=/home/user/vault-work
```

### PDF-книги

```yaml
sources:
  book_paths:
    - /home/user/Books
    - /mnt/library/tech
```

Или:

```
KB_SOURCES__BOOK_PATHS__0=/home/user/Books
KB_SOURCES__BOOK_PATHS__1=/mnt/library/tech
```

Индексация запускается автоматически через watchdog при изменении файлов.
Переименование/перемещение бесплатно — адресация по SHA-256 содержимого.

### Веб-страницы (вручную)

В чате Open WebUI введи команду:
```
/save-web https://example.com/article
```
Страница сохранится в `data/web-saved/<sha256>/page.md` и пройдёт индексацию.

---

## Куда что кладётся

```
data/
├── qdrant/          # dense + sparse векторные индексы (embedded Qdrant)
├── oxigraph/        # RDF-граф (6 named graphs: tier0/1/2, predictions, ...)
├── sqlite/app.db    # очередь задач, кэши, KGE-очередь ревью
├── books/<sha256>/  # распарсенные PDF (chapters, metadata, images)
├── web-saved/<sha256>/ # сохранённые веб-страницы
├── kge/runs/        # модели RotatE, метрики, predictions.parquet
├── models/          # веса HuggingFace (Qwen3-Embedding, Qwen3-Reranker)
└── logs/            # app.jsonl, llm.jsonl (structured logs)

evals/
├── gold/q-*.yaml    # 85 золотых вопросов для оценки RAG
└── runs/<ts>/       # результаты прогонов eval

infra/config.yaml    # основная конфигурация (vault'ы, книги, модели)
.env                 # переменные среды (KB_PROFILE, переопределения)
```

---

## Запуск

### Через systemd (рекомендуется)

```bash
systemctl --user start kb-app.service
systemctl --user start kb-searxng.service
systemctl --user start kb-open-webui.service
```

Автозапуск при логине:
```bash
systemctl --user enable kb-app.service kb-searxng.service kb-open-webui.service
```

### Напрямую (разработка)

```bash
uv run python -m app run             # сервер на :8000
uv run python -m app run --reload    # с hot-reload (только dev)
uv run python -m app run --port 8001 # другой порт
```

### Адреса

| Сервис         | URL                                |
| -------------- | ---------------------------------- |
| API + UI       | http://localhost:8000              |
| Open WebUI     | http://localhost:3000              |
| Healthcheck    | http://localhost:8000/healthz      |
| Jobs-панель    | http://localhost:8000/jobs         |
| KGE-ревью      | http://localhost:8000/review       |
| Tier-1 ревью   | http://localhost:8000/review/tier1 |
| SPARQL-консоль | http://localhost:8000/sparql       |

---

## Проверка

### Базовая

```bash
curl http://localhost:8000/healthz
```

### Статус обработки источников

Веб-панель с авто-обновлением (рекомендуется):
```
http://localhost:8000/jobs
```

CLI (без запущенного сервера):
```bash
uv run python -m app status              # счётчики по статусам
uv run python -m app status --failed     # + детали последних ошибок
```

JSON API:
```bash
curl http://localhost:8000/jobs/stats
# {"pending": 5, "running": 1, "done": 1234, "failed": 0, "total": 1240}
```

Источник считается обработанным, когда все связанные задачи имеют статус `done`.
Задачи в очереди: `index_note`, `parse_book`, `embed_chunks`, `extract_tier0`.

### Диагностика конфигурации

```bash
uv run python -m app diag profile    # профиль + расхождения с дефолтами
uv run python -m app diag versions   # версии установленных компонентов
```

### KGE-управление

```bash
uv run python -m app kge train       # обучить модель вручную
uv run python -m app kge runs        # список прогонов с метриками
uv run python -m app kge rollback    # откатить к предыдущей модели
```

### Запуск eval

```bash
uv run python -m app eval run                    # полный прогон 85 вопросов
uv run python -m app eval sentinels              # только sentinel-кейсы (10 шт)
uv run python -m app eval run --question q-001   # один вопрос
```

Результаты сохраняются в `evals/runs/<timestamp>/`.

### Тесты

```bash
uv run pytest
```

---

## Конфигурация

Система использует три слоя конфигурации (применяются в таком порядке):

1. `infra/config.yaml` — базовые значения
2. `infra/profiles/<KB_PROFILE>.yaml` — профиль железа
3. `.env` (переменные `KB_*`) — пользовательские переопределения

Ключевые переменные `.env`:

```bash
KB_PROFILE=laptop-4070-8gb            # профиль железа
KB_OLLAMA__HOST=http://localhost:11434 # если Ollama на другом хосте
KB_LOG__LOG_LLM_FULL=true             # полное логирование LLM-промптов (для отладки)
```

Для Eval LLM-судьи (опционально — нужен внешний LLM):
```bash
EVAL_JUDGE_BASE_URL=https://api.anthropic.com/v1
EVAL_JUDGE_API_KEY=sk-ant-...
EVAL_JUDGE_MODEL=claude-sonnet-4-6
```

---

## Обновление

```bash
./scripts/upgrade.sh
```

## Удаление

```bash
./scripts/uninstall.sh
```
