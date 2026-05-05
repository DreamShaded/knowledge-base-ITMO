# PRD: Семантическая система управления персональной базой знаний

> Product Requirements Document, v2 (правки после brainstorm-аудита 2026-04-28/29)
> Проектная работа по курсу «Базы знаний и данные», магистратура ИТМО
> Автор: Роман Троицкий, Р4109
>
> Источник правок: `plans/reports/brainstorm-260428-1128-prd-audit.md`. Технические решения вынесены в ADR-0001..0011.

---

## 1. О чём этот документ

Документ описывает **что** создаётся, **зачем** и **по каким критериям судим об успехе**. Конкретные технические решения (PDF-парсер, chunking, re-ranker, KGE-модель, веб-провайдер, faithfulness threshold и т.д.) живут в Architecture Decision Records (`docs/adr/NNNN-*.md`) и системной архитектуре (`docs/architecture.md`).

Дисциплина разделения документов:

| Документ | Что | Что нет |
|----------|-----|---------|
| `PRD.md` (этот) | Проблема, целевой пользователь, сценарии, метрики приёмки, ограничения, скоуп. Из стека — только обязательные семейства технологий из требований курса. | Конкретные библиотеки, версии, threshold-ы, schema, алгоритмы. |
| `docs/architecture.md` | Слои, потоки данных, компоненты, sequence-диаграммы основных сценариев. _Как устроено._ | Альтернативы и обоснования (это в ADR). |
| `docs/adr/NNNN-*.md` | По одному решению на файл: Context / Options / Decision / Consequences. _Почему именно так._ | Обзор системы. |

Зафиксированные на момент написания PRD ADR: 0001 PDF-парсер (Marker), 0002 chunking (parent-child, Qwen3 tokenizer), 0003 embedding & reranker (Qwen3-Embedding + Qdrant BM25 sparse + Qwen3-Reranker, per-hardware-profile size variants), 0004 source hierarchy & router, 0005 web search (SearXNG), 0006 citation & answer schema, 0007 faithfulness check, 0008 graph tiers & ontology, 0009 KGE model (RotatE), 0010 eval protocol, 0011 vector store & storage layer (Qdrant + minimal SQLite, host-only), 0012 multi-source ingestion (несколько vault'ов и book-paths с общим индексом), 0013 service providers & resource governance (DI ports для multi-impl сервисов, per-provider semaphores, `GpuGovernor` / `CircuitBreaker` FSM, hardware-tuned defaults).

---

## 2. Проблема

### 2.1. Контекст пользователя

Современные практики персонального управления знаниями (Personal Knowledge Management, PKM) предполагают, что пользователь годами накапливает корпус заметок и литературы по разным областям интересов: рабочие проекты, образование, исследования, личные интересы. Типичный объём такого корпуса для активного практика — несколько тысяч заметок и десятки книг.

В индустрии устоялись два инструмента для работы с таким корпусом:

- **Obsidian** — редактор Markdown-заметок с локальным хранилищем. Поддерживает явные ссылки между заметками через wiki-links `[[название]]`. Файлы лежат в обычной файловой системе и могут версионироваться через git.
- **PARA-методология** (Tiago Forte) — структура папок: **P**rojects (активные проекты с дедлайном), **A**reas (постоянные сферы ответственности), **R**esources (справочные материалы по интересам), **A**rchives (завершённое).

Пользователи также накапливают коллекцию книг в PDF — техническую литературу, монографии, учебники.

### 2.2. Главная проблема: разрывы знаний между папками

PARA организует заметки по **функциональному назначению** (проект, область, ресурс), а не по **семантике содержания**. Из-за этого тематически близкие заметки оказываются в структурно разных местах и теряют связь.

Конкретный пример из практики автора:

- Заметка «Реактивность» в проекте **корпоративного курса по фронтенду** (объясняет реактивность в контексте Vue.js для слушателей курса)
- Заметка «Реактивность» в проекте **магистерской диссертации в ИТМО** (про реактивные системы в контексте Web Components и SDK-разработки)

Обе заметки про одно концептуальное явление, но между ними нет явной wiki-link связи. Когда автор пишет третью заметку на похожую тему — он не вспоминает про обе предыдущие и пишет с нуля. Знание дублируется и фрагментируется.

Эта проблема **не решается полнотекстовым поиском**: в первой заметке может быть слово «реактивность», во второй — «reactive», в третьей — «обновление DOM», и они формально не пересекаются по тексту, но семантически связаны.

### 2.3. Дополнительные проблемы

- **Выдержки из книг живут отдельно от заметок.** Прочитал главу про реактивные системы в книге Бринкманна — заметка осталась в Resources, не связана с проектными заметками, через полгода забылась.
- **Контекст внешнего мира отсутствует.** Заметка упоминает «Eric Evans» — без явного линка на Wikidata непонятно, кто это, какие у него ещё работы, какие связанные концепции.
- **Свежий контекст недоступен.** Vault и локальная LLM имеют горизонт «до момента создания»: на запрос «что нового по теме X в 2026?» не хватает источника. Нужен опциональный, опт-ин веб-канал с цитатами и сохранением приватности запроса.
- **Ответ модели без верификации непрозрачен.** Даже если LLM нашёл правильные источники, читателю непонятно, какое утверждение из какого источника. Нужен **структурированный ответ с inline-цитатами** и пост-проверкой (faithfulness): каждое фактическое утверждение либо опирается на источник, либо помечено как «общее знание модели».
- **LLM-чаты по vault'у дают слабые ответы.** Существующие RAG-обвязки (типа Open WebUI с подключением документов) ищут только по векторной близости и не делают re-ranking, multi-channel routing, faithfulness-проверку. На multi-hop вопросах («как тема X из заметок 2023 связана с проектом Y из 2025?») они не работают.

---

## 3. Кому это нужно

### 3.1. Целевой пользователь

**Knowledge worker с многолетней Obsidian-практикой и большим vault'ом** (3+ года ведения, 1000+ заметок). Типовой профиль:

- Технический специалист (разработчик, исследователь, аналитик, преподаватель)
- Использует PARA или похожую структуру
- Параллельно читает 5-15 технических книг в год, делает выписки
- Заинтересован в локальной обработке данных (приватность важнее облачного удобства)
- Имеет домашний сервер или мощный десктоп (необязательно, но желательно: GPU для LLM)

### 3.2. Пользовательские сценарии

Sentinel-кейс «Реактивность» (см. §4.2 / §11) проходит через все сценарии — концептуально близкие заметки в разных проектах должны соединяться без явной wiki-link.

**Сценарий 1: Поиск с пониманием смысла.**
Пользователь спрашивает: «Что я писал про state management в реактивных системах?» Система понимает, что искать нужно не только заметки со словом «state management», но и связанные концепты: реактивность, observers, signals, immutable data. Возвращает релевантные фрагменты из всех проектов, не только из текущего.

**Сценарий 2: Обнаружение скрытых связей.**
Пользователь открывает заметку «Реактивность во Vue» в корпоративном проекте. Система предлагает: «Похожая заметка существует в проекте магистратуры — “Реактивность в Web Components”. Связать?» Пользователь видит автоматически найденную связь, которую сам бы не заметил, и решает, добавлять ли её.

**Сценарий 3: Расширение контекста (Wikidata).**
Пользователь читает заметку про DDD и упоминание Eric Evans. Система автоматически показывает: биографию (из Wikidata), другие книги автора, связанные концепции (CQRS, Event Sourcing), упоминания этих концепций в других заметках vault'а.

**Сценарий 4: Контекстный диалог по корпусу.**
Пользователь задаёт вопрос локальному LLM: «Сравни подходы к event-driven архитектуре в книгах, которые я читал». Система собирает контекст из релевантных глав книг и заметок (учитывая графовые связи), передаёт в LLM, получает структурированный ответ с inline-цитатами на конкретные источники.

**Сценарий 5: Свежий контекст из веба.**
Запрос: «Какие новые подходы к Module Federation в Vue появились в 2026?». Vault не содержит свежего → роутер активирует веб-канал (SearXNG). Перед outbound запрос проходит **sanitization** (вычищаются `[[wiki-links]]`, `#tags`, имена из whitelist). Результаты возвращаются как UNTRUSTED-источники с явной маркировкой и timestamp получения. Командой `/save-web <url>` пользователь может сохранить страницу в vault как curated source (TRUSTED-WEB-SAVED).

**Сценарий 6: Структурированный ответ с цитатами и confidence.**
На любой запрос система возвращает Markdown-ответ фиксированной структуры: TL;DR → основной ответ с inline-цитатами `[^N]` → (опционально) «Из общих знаний» → «Связанные материалы» → блок «Источники» с verbatim-цитатами и кликабельными ссылками → строка confidence. Если retrieval вообще пустой — система явно говорит «не нашёл», а не галлюцинирует.

**Сценарий 7: Подтверждение скрытых связей (KGE `/review`).**
Раз в неделю (или после значительного изменения графа) система предлагает в `/review` UI top-предсказания связей с обоснованием («общие концепты [реактивность, observers]»). Пользователь подтверждает (связь повышается в Tier-0), отклоняет (помечается, не повторяется без сильного роста score) или откладывает (вернётся через 7 дней).

---

## 4. Цели и метрики

### 4.1. Цели проекта

**Основная цель:** Локальная система, которая превращает Obsidian-vault и PDF-библиотеку в связный граф знаний с многоканальным retrieval (vector + sparse + graph + опциональный web), структурированными ответами LLM с inline-цитатами и faithfulness-проверкой, плюс автоматическим обнаружением неявных связей через KGE.

**Подцели:**

1. **Унификация хранилища** — заметки, главы книг, (опционально) сохранённые web-страницы в едином индексе с провенансом и trust-tiers.
2. **Извлечение структуры** — детерминированный Tier-0 граф из явных сигналов + LLM-OpenIE Tier-1 с multi-gate валидацией (без отравления KGE).
3. **Обогащение Wikidata** — Tier-2 enrichment концептов в трёх уровнях (L1 core / L2 type-specific / L3 lazy), 1-hop hard rule.
4. **Многоканальный hybrid retrieval** — soft-routing по каналам {notes, books, wikidata, web} + cross-encoder re-rank + parent-child aggregation + MMR.
5. **Структурированные ответы с цитатами** — фиксированный Markdown-формат, three-zone grounding (grounded core / parametric framing / explicit fallback), faithfulness check с tiered warnings.
6. **Опциональный web-канал** — приватность запроса (sanitization), trust-policy, transient-default + explicit `/save-web` для архивации.
7. **Обнаружение неявных связей** — KGE-модель (RotatE) на чистом Tier-0 ∪ Tier-2; предсказания через UI подтверждения с adaptive surfacing.
8. **Замкнутый eval-loop** — 85 размеченных вопросов в 7 категориях + LLM-judge calibration + sentinel-кейсы как regression.

### 4.2. Метрики успеха

**Пользовательские (продуктовая полезность):**

| Метрика | Цель | Как измеряем |
|---------|------|--------------|
| `must_include_recall` (gold sources в final answer) | ≥ 0.85 | Структурно по 85 размеченным вопросам |
| Hallucination rate | ≤ 0.05 | External LLM-judge (OpenAI-compatible endpoint, vendor-agnostic; recommended default `claude-sonnet-4-6`) + 30% spot-check автором |
| Coverage `expected_facts` | ≥ 0.70 | LLM-judge external |
| Answer relevance (1-5, pairwise) | ≥ 4.0 avg | LLM-judge external |
| Faithfulness | ≥ 0.90 | Per ADR-0007 + 20% peer-review |
| Sentinel «Реактивность» | связь найдена через graph hop | Качественный тест |
| Refusal correctness (отказ на пустом retrieval) | 1.0 | 5 unrelated + 3 adversarial |
| Sanitization leak rate (приватные маркеры в web-запросе) | 0.0 | Regex-чек 6 privacy-кейсов |
| Cohen's κ author ↔ LLM-judge | ≥ 0.6 | Calibration loop pre-defense |
| KGE precision@K автор top-50 (structured rubric) | ≥ 0.5 | Deferred 1-2 недели |

**Технические:**

| Метрика | Цель | Зачем |
|---------|------|-------|
| Latency p95, local-only | ≤ 9s | Интерактивность чата |
| Latency p95, with-web | ≤ 17s | Web добавляет fetch + extraction |
| Задержка watchdog → индекс | ≤ 30s | Синхронность изменений |
| Доступность системы | ≥ 99% при работающем сервере | Domestic-server SLA |

**Академические (KGE как наблюдение, не таргет):**

| Метрика | Цель | Комментарий |
|---------|------|-------------|
| Sanity FB15k-237 | MRR > 0.30, Hits@10 > 0.50 | Корректность реализации (литературные диапазоны RotatE) |
| MRR / Hits@1/3/10 / AMRI на personal graph | _фиксируем как наблюдение_ | Без подгонки; честный отчёт о работе RotatE на специфике персонального vault'а |
| Tier-0 размер | ≥ 5000 троек | + ≥ 80% корректны на ручной выборке 100 |
| Wikidata coverage концептов | ≥ 30% | + точность ≥ 90% на выборке 50 проверенных линков |

Все KGE-метрики — в **filtered + realistic** ranking режиме (стандарт публикаций).

> Удалена цель **«сравнение ≥4 KGE-моделей с таблицей метрик»** (исходный §11 п.5). Учебная цель — _применить_ KGE на персональном графе, не сравнивать модели. Одна модель + sanity check на публичном бенчмарке.

### 4.3. Что НЕ является целью

Чтобы избежать раздувания скоупа, явно фиксируем границы:

- **Не создаём замену Obsidian.** Obsidian остаётся основным редактором, наша система — слой поверх vault'а.
- **Не создаём систему совместной работы.** Single-user, без синхронизации между устройствами на уровне приложения (есть git).
- **Не создаём публичный сервис.** Развёртывание только локальное, на одного пользователя.
- **Не делаем мобильный клиент.** Доступ через браузер на том же сервере, где крутится Obsidian.
- **Не покрываем все форматы.** Markdown и PDF + опциональный сохранённый веб. EPUB / FB2 / DOCX — задел на будущее.
- **Не делаем редактирование графа через UI.** Граф редактируется через изменение заметок и подтверждение/отклонение KGE-предсказаний. Прямого редактирования троек руками нет.
- **Не делаем сравнение KGE-моделей** — одна модель (RotatE) + sanity check.
- **Не подключаем managed web-providers** (Tavily / Brave / Exa) в MVP. Только local SearXNG + trafilatura + Playwright; managed остаётся opt-in env-хуком на будущее.
- **Не пишем automated regression tests / pre-commit / CI.** Eval — measurement по требованию, не блокирующий gate (см. ADR-0010).

---

## 5. Что будет в результате

### 5.1. Компоненты (с точки зрения пользователя)

**Фоновая индексация.** Watchdog отслеживает изменения в **наборе vault'ов** (например, личный + рабочий — настраивается списком в конфиге, см. ADR-0012) и в **наборе папок с книгами** (одна или несколько, путь не имеет значения — content-hash дедупит) → задача в SQLite-job-queue (`data/sqlite/app.db`) → chunking (parent-child, Qwen3 tokenizer) → dense embedding (Qwen3-Embedding 0.6B на laptop / 4B-MRL-1024 на desktop) + sparse tokenization (FastEmbed Bm25) → upsert в Qdrant (collection `chunks_v{N}` с автоверсионированием при schema-changes, named vectors `dense`+`sparse` в одной точке, sparse считается через `Modifier.IDF` серверно, payload включает `vault_id`) → обновление Tier-0 графа в Oxigraph. Книги индексируются по content-hash (переименования и наличие копий на разных дисках бесплатны); web-saved страницы — аналогично.

**Чат-интерфейс.** Open WebUI в браузере. Под капотом каждого ответа:

1. **Query understanding** — rule-based confidence scorer + LLM-fallback при низкой уверенности (qwen3:8b с structured output).
2. **Soft-routing** — веса каналов {notes, books, wikidata, web}; не «либо всё либо ничего», а взвешенное участие.
3. **Multi-signal web trigger** + **sanitization gate** (приватные маркеры удаляются в strict-режиме до outbound).
4. **Параллельный retrieval** по активным каналам → RRF-fusion → cross-encoder re-rank → parent-child aggregation → MMR-diversity.
5. **Three-zone generation** (grounded core / parametric framing / explicit fallback).
6. **Faithfulness check** (atomic decomposition → batch claim ↔ quote → tiered warnings + cross-source check для critical).
7. **Render** Markdown с inline footnote-citations, confidence-блоком, секцией «Связанные материалы».

**`/review` UI для KGE-предсказаний.** Top-предсказания вида `Note A ↔ Note B, score 0.87, обоснование «общие концепты [реактивность, observers]»`. Действия: approve (связь → Tier-0), reject (Tier-1-rejected, не идёт в training), defer (вернётся через 7 дней). Adaptive per-entity квота (1-7 предсказаний по top-1 normalized score) + round-robin diversity + skip already-reviewed + re-show при значимом росте score.

**SPARQL-консоль.** Прямой доступ к графу для отладки и продвинутых ad-hoc запросов («все заметки в проекте X, упоминающие концепт Y, написанные после даты Z»).

**Slash-команды (per ADR-0004 / 0005 / 0007):**

| Команда | Эффект |
|---------|--------|
| `/sources notes,books` | Override soft-router: явный список каналов |
| `/scope vault:<id>[,<id>...]` | Ограничить retrieval подмножеством vault'ов (см. ADR-0012); default — все |
| `/web` / `/no-web` | Принудительно вкл/выкл веб для запроса |
| `/save-web <url>` | Архивировать страницу в vault как curated source |
| `/explain` | Educational mode: увеличить вес Z3 (parametric fallback) |
| `/strict` | Lookup mode: только Z1, отключить Z2/Z3 |
| `/no-check` | Отключить faithfulness check (отладка / скорость) |
| `/deep` / `/explore` | Расширить graph walk (2 / 3 hops, увеличенный budget) |

### 5.2. Формат ответа (UX-обещание; детали — ADR-0006)

Каждый ответ — Markdown фиксированной структуры:

```markdown
## TL;DR
<2-3 предложения, могут содержать [^N]>

## Ответ
<основная часть; grounded факты с [^N], parametric связки без меток>

## Из общих знаний (не из источников)         ← опционально, Z3
> Парафраз parametric-знания LLM, явно отделённый от vault'а.

## Связанные материалы                        ← если retrieval нашёл лишние parents
- [Title](link) — одно предложение reason от LLM

## Источники
[^1]: **[Title](primary-link)** · [preview](preview-link)
    *modified_at · score X.XX*
    > verbatim quote (≤140 chars)

## Уверенность
**low|medium|high** — N заметок, M глав книг, web использовался?
```

Если retrieval пустой — секция **«Не нашёл в источниках»** вместо галлюцинации, с подсказкой переформулировать или использовать `/explain`.

Faithfulness warnings рендерятся как `[^N] ⚠` рядом с непокрытыми claims; для дат/чисел/имён может включаться inline-маркер (`Vue 3 был выпущен в **2020 году ⚠**`). Если ≥50% claims с warning — top-level aggregate-блок над «Ответ».

Note-ссылки — две: `obsidian://open?...` (для desktop-редактирования) + preview через web-route (для удалённого доступа без Obsidian-клиента).

### 5.3. Иерархия источников и доверие

| Tier | Источник | Score multiplier | Discipline LLM |
|------|----------|------------------|----------------|
| TRUSTED | Заметки + аннотации на книгах | ×1.00 | Можно цитировать как факт |
| TRUSTED-EXT | PDF-книги | ×0.95 | Можно цитировать; для критичных — желательна сверка |
| TRUSTED-WEB-SAVED | Web-страницы, явно сохранённые `/save-web` | ×0.90 | Curated trust |
| SEMI | Wikidata | ×0.85 | Цитировать как «справочные данные» |
| UNTRUSTED | Transient web (SearXNG) | ×0.70 | Цитировать с явной меткой `[external, fetched: …]` |
| PARAMETRIC | LLM internal (Z2 framing, Z3 fallback) | — (не в retrieval) | Three-zone grounding |

Multipliers применяются после re-ranker'а как ranking nudges, не блокировки — несколько источников остаются видимы LLM, который сам взвешивает.

### 5.4. Что пользователь не увидит, но это работает

- Qdrant collection `chunks_v{N}` (initial `chunks_v1`; auto-version bump при schema-changes — ADR-0011 §1) с **named vectors** `dense` (1024-dim Qwen3-Embedding, cosine) и `sparse` (Qdrant native BM25 через FastEmbed Bm25 + `Modifier.IDF` серверно). Dense и sparse — два независимых провайдера; на indexing forward pass dense (GPU) и BM25 tokenization (CPU) идут параллельно. Hybrid retrieval — через Qdrant Query API с `prefetch` + `Fusion.RRF` (нативно, без самописного слияния).
- Cross-encoder re-ranker Qwen3-Reranker family (на laptop — `Qwen3-Reranker-0.6B` CPU, на desktop — `Qwen3-Reranker-4B` GPU; top-50 → top-30).
- Parent-child aggregation: child = 512 токенов с overlap 64, parent = секция или вся заметка; promotion до главы при множественных матчах.
- MMR (λ=0.6) на финальных parent'ах для diversity между проектами.
- RDF-граф в Oxigraph: 5 named graphs — `<urn:tier0>`, `<urn:tier1>`, `<urn:tier1-pending>`, `<urn:tier1-rejected>`, `<urn:tier2>` + `<urn:predictions>`.
- LLM-OpenIE pipeline с **5 gates** auto-promotion: adaptive confidence per triple type, predicate whitelist, embedding-canonicalization, cross-source agreement boost, sanity checks.
- Tiered Wikidata enrichment: L1 core (всегда) + L2 type-specific (по `P31/P279`) + L3 rich (lazy при RAG-time).
- KGE-модель RotatE 256-dim с adaptive retraining (skip / warm-start incremental / full retrain / full + HPO по weighted change_score).
- Job-queue + кеши (web search 24h, web page 7d, faithfulness 7d) в одном файле `data/sqlite/app.db` (WAL, single-writer worker для очереди); content-hash addressing для книг и web-saved.
- Faithfulness pipeline: atomic decomposition → batch claim ↔ quote → tiered fallback (partial → corpus check) → optional cross-source check для negation/factual_specific.
- Web search/page caches (24h / 7d) для повторных запросов.
- Graph walk policy: typed predicate weights, hops budgets per intent, hub blacklist, query-relevance re-ranking (`α × path_score + (1−α) × cosine`).

---

## 6. Технологический стек

### 6.1. Обязательные технологии (фиксированы курсом и ТЗ)

| Категория | Технология | Роль |
|-----------|------------|------|
| **Triple store / RDF** | **Oxigraph** (RocksDB) | Хранение графа, SPARQL 1.1, 5 named graphs (Tier-0/1/2 + predictions) |
| **Векторная БД** | **Qdrant** (embedded local mode) | Dense + sparse в одной collection через named vectors; payload-фильтры (`source_type`, `trust_tier`, `chapter_id`, …); hybrid через Query API + Fusion.RRF. См. ADR-0011. |
| **Knowledge Graph Embedding** | **PyKEEN** | Одна модель (RotatE) + sanity check FB15k-237 |
| **Внешний источник знаний** | **Wikidata** (SPARQL endpoint) | Tier-2 enrichment концептов, 1-hop, L1+L2 |
| **Локальная LLM** | **qwen3:8b** через Ollama | OpenIE, RAG generation, query understanding, faithfulness checks |
| **Эмбеддинг-модель (dense)** | **Qwen3-Embedding** family (Apache 2.0) — `Qwen/Qwen3-Embedding-0.6B` на laptop 4070 8GB (native dim 1024); `Qwen/Qwen3-Embedding-4B` на desktop-mid+ (MRL-truncate до 1024 для совместимости индекса) | Multilingual ~100 языков, instruction-aware retrieval, контекст до 32K, last-token pooling. Sparse-канал — отдельный провайдер (Qdrant native BM25, см. строку ниже). |
| **Sparse-канал** | **Qdrant native BM25** (FastEmbed `Bm25` client + `SparseVectorParams(modifier=Modifier.IDF)` серверно) | Lexical recall, family-independent от dense. Snowball-стеммер для русского — кастомный FastEmbed tokenizer. См. ADR-0003, ADR-0011. |

### 6.2. Поддерживающие технологии (выбор автора)

| Категория | Технология | Зачем |
|-----------|------------|-------|
| Файловый watcher | Python `watchdog` | Изменения в vault и папке книг |
| Cross-encoder reranker | **Qwen3-Reranker** family — `Qwen/Qwen3-Reranker-0.6B` на laptop (CPU, 250-700 ms на top-50); `Qwen/Qwen3-Reranker-4B` на desktop (GPU, 300-500 ms) | +15-30% Recall@10 после top-50 (применяется поверх Qdrant fused top-50). Family-consistent с embedder'ом. |
| PDF-парсер | **Marker** (Surya layout/OCR) | LaTeX-формулы, главы, page-anchors |
| Web search (default) | **SearXNG** self-hosted (host-installed via systemd, без Docker) | Анонимизирующий мета-поиск (Google/Bing/DDG/Wikipedia/arxiv) |
| Web extraction | **trafilatura** + **Playwright** fallback | 100% local, без managed-providers |
| Job-queue + ops caches | **SQLite WAL**, один файл `data/sqlite/app.db` | Очередь watchdog → indexer (single-writer); web search / web page / faithfulness caches с TTL. См. ADR-0011. |
| Web-фреймворк | FastAPI | REST API, async, OpenAPI |
| LLM-абстракция | `instructor` (поверх OpenAI-compatible API) | Structured output, провайдер-нейтральность |
| Python toolchain | **uv** (Astral) + `uv.lock` | Воспроизводимое окружение на новой машине: `uv sync` → готово |
| Конфигурация | `pydantic-settings` + `.env` | Типизированный конфиг |
| Логирование | `structlog` | Structured logs (без OTel в MVP) |
| LLM-фронтенд | Open WebUI (host-installed via `uv tool install open-webui`) | Готовый чат-UI к Ollama |
| Process supervision | **systemd** user/system units (без Docker) | App, SearXNG, Open WebUI, Ollama — host-installed |

**Future work (вынесено из MVP):**

- **MLflow tracking** — для одной KGE-модели достаточно JSON-логов в `data/kge/runs/<timestamp>/` + `current → runs/<latest>/` симлинка. MLflow подключим, когда появится сравнительная KGE-задача.
- **Prometheus + Grafana + OpenTelemetry** — operational over-engineering для solo-сервера. Structured-logs + один dashboard в Open WebUI достаточны на MVP.
- **Managed web providers** (Tavily / Brave / Exa) — env-хук готов (`WEB_SEARCH_PROVIDER`), подключение по необходимости, если SearXNG окажется ненадёжен.
- **Qdrant server mode** — `QDRANT_MODE=server` env-switch готов; embedded local mode (`data/qdrant/`) на MVP. Server (отдельный процесс / systemd unit) подключим, если понадобится REST snapshot API или удалённый доступ.
- **Postgres вместо SQLite** — пересмотрим, если single-writer нагрузка перестанет помещаться в SQLite (в MVP это исключено: один indexer worker, один пользователь).
- **Multi-vector ColBERT embeddings** — storage ×10 не оправдывает MVP-прирост; резерв на случай плато качества.
- **Vision-LLM для книг** (OCR-quality validation, картинки в контекст) — qwen3:8b text-only; перейдём при апгрейде модели.

### 6.3. Целостный стек как преимущество

Сочетание «Oxigraph + Qdrant + PyKEEN + Wikidata + локальная LLM + SearXNG + Marker» — нишевое и нестандартное. Существующие RAG-фреймворки (HippoRAG, GraphRAG, LightRAG — см. §9) либо используют свои собственные хранилища без RDF/SPARQL, либо не работают с Knowledge Graph Embedding, либо требуют отправки данных в облако, либо не дают structured-citation pipeline.

Самостоятельная сборка под этот конкретный стек **методически оправдана** для академической работы: позволяет глубоко проработать каждый компонент, избежать привязки к чужому фреймворку и продемонстрировать владение всеми обязательными технологиями курса.

### 6.4. Trust levels: LLM-провайдеры и retrieval-источники

**LLM-провайдеры (расширение ТЗ).** Все LLM-вызовы по умолчанию идут на `qwen3:8b` через Ollama (`max_trust=LOCAL`). Это явное требование ТЗ («полностью локальное развёртывание без передачи данных во внешние LLM-сервисы») и базовая гарантия приватности vault'а.

Архитектура клиента LLM спроектирована **расширяемой**: переключение на другие провайдеры (vLLM на удалённом сервере, managed API типа OpenAI/Anthropic) выполняется через изменение `.env` без правки кода — но только явным коммитом в git, не «случайно поменял переменную окружения». Каждой задаче (OpenIE, RAG-generation, query understanding, entity disambiguation, faithfulness, eval-judge) задаётся `max_trust`. Текущие defaults:

- `LOCAL` — все production-задачи (OpenIE, generation, query understanding, faithfulness, in-app disambiguation).
- `PUBLIC_MANAGED` — **eval-judge** (OpenAI-compatible endpoint, конфигурируется в `.env` через `EVAL_JUDGE_BASE_URL` + `_API_KEY` + `_MODEL`; vendor-agnostic — Anthropic / OpenAI / OpenRouter / self-hosted vLLM. Recommended defaults: `claude-sonnet-4-6` primary, опционально `gpt-4o` для cross-vendor agreement). Eval-only, не in production hot path. Документированный opt-out на qwen3:8b при необходимости (с явной пометкой self-eval bias).

**Multi-provider обобщён (ADR-0013).** Тот же паттерн (per-task provider + trust enforcement) применяется не только к LLM, но и к embedder (dense/sparse), reranker, PDF parser — за DI ports. Resource governance расщеплена на `GpuMutex` (labeled priority mutex для local-providers) + `OllamaLifecycle` (FSM с unload/load side-effects под 8 GB VRAM ограничение; embedder Qwen3-0.6B co-resident, выгружается только qwen3:8b под Marker) + `CircuitBreaker` per remote provider + opt-in network layer. Hardware-tuned defaults в `infra/config.yaml` под текущий target (laptop 4070 8 GB VRAM) выбирают размер из Qwen3-Embedding/Reranker family (0.6B / 4B) под наличный VRAM; **3 GPU overlay-профиля** (laptop / desktop-mid / desktop-large) реализуются в phase-01 (см. план); CPU-only — future work (RAG на CPU не вписывается в latency-budget §4.2).

**Retrieval-источники (NEW).** См. §5.3 — теги `TRUSTED / TRUSTED-EXT / TRUSTED-WEB-SAVED / SEMI / UNTRUSTED / PARAMETRIC` с score multipliers. Web-канал — UNTRUSTED transient + privacy-gate (sanitization обязательна перед outbound).

---

## 7. Ограничения

### 7.1. Технические ограничения

- **Hardware:** домашний сервер с одним GPU (текущий — RTX-class card). LLM, эмбеддинги и KGE-тренировка делят один GPU. Тяжёлые операции (parsing батча книг через Marker, KGE retraining) запускаются по nightly-расписанию или idle-detect (≥5 минут без RAG-запросов).
- **Объём корпуса:** проектируем на масштаб «единицы тысяч заметок и десятки книг». Масштабирование на 100K+ документов — отдельная задача, не входит в скоуп.
- **Языки:** русский и английский. Другие языки могут работать через Qwen3-Embedding (multilingual ~100 языков), но не тестируются. Sparse-канал на BM25 без стемминга для не-ru/en — деградирует на морфологии; mitigation — расширение FastEmbed tokenizer.
- **Форматы:** Markdown и PDF + опциональный сохранённый веб (`/save-web`). EPUB / FB2 / DOCX — не входят.
- **Источники:** N независимых Obsidian-vault'ов (например, личный + рабочий) и N путей с книгами на разных дисках. Все попадают в общий retrieval-индекс с `vault_id` payload-фильтром (ADR-0012). Web-saved — один derived store, не multi.
- **Web-канал — single-provider (SearXNG) в MVP.** Healthcheck + systemd `Restart=always`; на отказ — graceful degradation (web channel weight = 0).
- **Daily quota web-запросов:** 50/день (env-tunable).
- **Faithfulness-check добавляет +2-3s к latency p95.** Если стабильно > 3s — async rendering (показывать ответ без warnings, обновлять когда готовы) или меньшая модель для checks.

### 7.2. Организационные ограничения

- **Автор и пользователь — одно лицо.** Это и преимущество (полное знание domain'а, real-world тестирование на собственном vault'е), и ограничение (peer-reviewer для аннотаций ограничен sanitized subset'ом, см. ADR-0010).
- **Сроки:** проектная работа в рамках одного семестра, ~12 недель.
- **Время:** ~15-20 часов в неделю, параллельно с основной работой автора.
- **Eval-budget:** ~24h автора + ~5h peer-reviewer'а на разметку (gold RAG 85 Q + OpenIE 500 + KGE top-50 + faithfulness 100), 5-6 недель wall-clock.

### 7.3. Приватность

- **Все production-данные обрабатываются локально**, vault не покидает сервер.
- **Web-канал:** запрос проходит **sanitization gate** перед outbound. Удаляются `[[wiki-links]]`, `#tags`, имена из whitelist `sensitive_entities`; first-person pronouns обезличиваются. В strict-режиме — если sanitization не справилась, web-call blocked + UI warning. SearXNG self-hosted → outbound идёт через локальный прокси, но всё равно к внешним engines (Google/DDG); первое использование показывает явный warning.
- **Wikidata:** запросы содержат только название концепта (например, «Domain-Driven Design»), не контекст и не текст заметки.
- **Eval LLM-judge** (PUBLIC_MANAGED): ~85 калибровочных calls в семестр на тестовых вопросах; trust level явно поднят, документированный opt-out на qwen3:8b возможен.
- **Логи:** структурированные, без полного текста заметок (исключение — отдельная JSONL-структура с LLM-вызовами, физически локально, для отладки промптов).

---

## 8. Главные риски

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| Локальная LLM (qwen3:8b) даёт слишком много шума при OpenIE | Средняя | Высокое (грязь в Tier-1) | **Multi-gate auto-promotion** (ADR-0008): adaptive confidence per triple type, predicate whitelist, embedding-canonicalization, cross-source agreement boost, sanity checks. Tier-1 НЕ идёт в KGE-training (изоляция шума). Manual `/review` UI для grey-zone. |
| Граф слишком маленький для качественного KGE | Низкая | Высокое | Tier-0 из явных сигналов (теги/проекты/wiki-links) + Tier-2 Wikidata (1-hop, L1+L2) дают плотные связи. Sanity-check FB15k-237 как baseline корректности. На малом personal graph метрики KGE — observation, не таргет. |
| Hybrid не лучше vector-only | Средняя | Среднее | Eval-сет включает 17 multi-hop (с sentinel «Реактивность»), 17 personal recall, 10 fresh/web — гарантированный subset, где hybrid должен побеждать. Если результат неоднозначен — это honest scope statement в защите. |
| Wikidata-disambiguation ошибается на коротких терминах | Высокая | Низкое-Среднее | Confidence threshold + контекстная LLM-disambiguation + UI ручной коррекции (Tier-1 review extends на Wikidata-disambig). |
| Watchdog нестабилен при массовых изменениях (`git pull` крупного батча) | Средняя | Среднее | SQLite-backed job-queue (`data/sqlite/app.db`) с single-writer worker; periodic full reindex по content-hash diff раз в сутки. |
| SearXNG падает / outbound недоступен | Средняя | Низкое | Healthcheck перед запросом → graceful: web channel weight = 0, ответ работает без web. UI warning. Env-хук на managed-fallback готов (но не подключен в MVP). |
| Faithfulness-check добавляет 2-3s latency | Высокая | Низкое | Atomic decomposition + batch claim↔quote (один LLM-call на пары); cache 7 дней; intent-based activation (skip для personal); async-рендеринг как escape hatch. |
| KGE-output (predictions) сам отравляет training | Средняя | Высокое | Predictions в `<urn:predictions>`, в KGE-training НЕ возвращаются (избегаем self-reinforcement). Approve переводит в Tier-0 явным действием пользователя. |
| Marker делит GPU с Ollama → конфликт | Средняя | Среднее | GPU policy: 1-3 книги — сразу; батч ≥ 5 — nightly или idle-detect (≥ 5 мин без RAG). KGE retraining defer'ится при активных RAG-запросах. |
| Скоуп не помещается в семестр | Средняя | Высокое | DAG этапов E1-E14 с критериями перехода (см. `docs/architecture.md`); MVP = E1-E8 (hybrid RAG + cite + web), академика — E9-E13. Защититься на MVP даже без E13. |

---

## 9. Связанные работы и контекст в индустрии

Чтобы было понятно, в каком ландшафте существует проект и почему выбрана самостоятельная реализация — краткий обзор существующих решений.

### 9.1. RAG-фреймворки с графом знаний

**HippoRAG** (Ohio State University, NeurIPS 2024). Open-source RAG-фреймворк, использующий граф знаний для улучшения retrieval. Извлекает тройки из текста через LLM (OpenIE), строит граф, и при запросе использует Personalized PageRank для расширения контекста.

*Релевантность.* HippoRAG не использует RDF/SPARQL (хранит граф в NetworkX in-memory), не использует Knowledge Graph Embedding, не интегрируется с Wikidata, не делает structured-citation pipeline и не имеет faithfulness-проверки. Эти отличия — методически принципиальные, поэтому HippoRAG не может быть взят как готовое решение. Однако **две идеи из HippoRAG заимствованы**: формат промпта для извлечения троек (LLM-OpenIE) и принцип entity-anchoring при обработке запроса. Эти заимствования экономят несколько недель на самой рутинной части.

**Microsoft GraphRAG.** Корпоративное решение для построения графа знаний из больших корпусов через LLM. Тоже без RDF/SPARQL, тоже без KGE, ориентировано на enterprise-нагрузки. Не подходит как готовое решение, но влияет на дизайн (например, идея иерархических community summaries — оставлено в future work).

**LightRAG.** Облегчённая альтернатива GraphRAG, dual-level retrieval. Те же ограничения по RDF/KGE.

### 9.2. Системы PKM с AI-расширением

**Notion AI, Mem.ai, Reflect.app.** SaaS-инструменты с AI-функциями поверх заметок. Все три — облачные, требуют отправки данных на серверы провайдера. Не подходят по требованию приватности.

**Obsidian + плагины (Smart Connections, Copilot).** Плагины к существующему Obsidian, добавляющие векторный поиск и LLM-чат. Не работают с графом, нет Wikidata-обогащения, нет KGE, нет structured-citation pipeline. Хорошие соседи, но решают другую задачу.

### 9.3. Knowledge Graph Embedding в персональных системах

KGE-методы (TransE, RotatE, ComplEx и т.д.) активно применяются в академической литературе на больших публичных графах (Wikidata, DBpedia, Freebase). Применение на **персональных** графах (десятки тысяч троек, специфичные домены, шумные данные) исследовано слабее. Это создаёт небольшую исследовательскую нишу: проверить, насколько хорошо одна продуманно выбранная KGE-модель (RotatE) работает на природе данных персонального vault'а.

### 9.4. Где находится проект

**Пересечение трёх областей:**

1. **PKM-инструменты** (Obsidian, PARA-методология) — пользовательский контекст.
2. **Academic Knowledge Graphs** (Wikidata, RDF/SPARQL, KGE) — формальная база курса.
3. **Современный RAG** (multi-channel retrieval + cross-encoder + structured citations + faithfulness + opt-in web) — технологический фундамент.

Большинство существующих систем покрывают одно или два из этих направлений. Проект целится именно в это пересечение.

---

## 10. Связь с курсом «Базы знаний и данные»

| Тема курса | Как покрыта в проекте |
|------------|------------------------|
| Семантические сети, RDF, RDFS | Формальная онтология PKM в Turtle (`docs/ontology.ttl`); реальный граф из тысяч троек; **5 named graphs** (Tier-0/1/1-pending/1-rejected/2 + KGE predictions) |
| SPARQL | Запросы к Oxigraph в retrieval pipeline + **typed graph walk** (predicate weights, hops budgets, hub blacklist); Wikidata enrichment через её SPARQL endpoint |
| Внешние KB (Wikidata, DBpedia) | Tiered enrichment концептов (L1 core / L2 type-specific / L3 lazy) с 1-hop hard rule; `wbsearchentities` + SPARQL |
| Knowledge Graph Embedding | RotatE на Tier-0 ∪ Tier-2 (PyKEEN); защищается выбор именно RotatE — все 4 типа реляций PKM-онтологии (симметричные, антисимметричные, инверсии, композиции) одной моделью; applied research, не сравнительный benchmark |
| Онтологии и таксономии | Кастомная PKM-онтология поверх FOAF, SKOS, Dublin Core; иерархия Note / Book / Chapter / Section / Chunk + Concept / Project / Tag |
| Интеграция гетерогенных источников | Markdown + PDF + Wikidata + (опц.) Web — единая модель с провенансом и trust-tiers |
| Information retrieval & evaluation | Multi-source RRF + cross-encoder re-rank + parent-child aggregation + MMR; eval-protocol с Cohen's κ, LLM-judge calibration, sentinel regression |

---

## 11. Критерии приёмки

Проект считается завершённым, когда выполняются все условия:

1. ✅ Watchdog-сервис автоматически индексирует изменения в **наборе vault'ов** и **наборе папок с книгами** (списки задаются в конфиге, см. ADR-0012) с задержкой не более 30 секунд; payload включает `vault_id` для retrieval-фильтрации.
2. ✅ Tier-0 граф в Oxigraph содержит ≥ **5000 троек** из реального vault'а; на ручной выборке 100 случайных ≥ **80% корректны**.
3. ✅ Wikidata enrichment покрывает ≥ **30%** концептов; на выборке **50** проверенных линков точность ≥ **90%**.
4. ✅ KGE-модель **RotatE** обучена и сохранена; sanity-check FB15k-237 в литературных диапазонах (MRR > 0.30, Hits@10 > 0.50). На personal graph метрики MRR / Hits@1/3/10 / AMRI зафиксированы как **наблюдение**, без подгонки.
5. ✅ KGE precision@K ≥ **0.5** на top-50 предсказаний по structured rubric (3 Y/N вопроса), с **deferred 1-2 недели** evaluation для отстранения от mindset на момент генерации.
6. ✅ Sentinel «Реактивность»: связь между двумя заметками из разных проектов автоматически найдена через **graph hop** в Tier-0 ∪ Tier-2.
7. ✅ Hybrid RAG доступен через Open WebUI; ответы — **структурированный Markdown** с TL;DR, inline-footnotes, secondary preview-links для заметок, секцией «Связанные материалы», блоком «Источники» с verbatim-цитатами и confidence-строкой.
8. ✅ **Faithfulness check** работает: на well-grounded ответах нет ложных ⚠ (S07); на unsupported claims — есть (S08); aggregate warning срабатывает при ≥50% claims без citations.
9. ✅ **Web-канал (SearXNG)** активируется по multi-signal trigger; **sanitization** приватных маркеров без leaks (regex-проверка 6 privacy-кейсов даёт 0.0 leak rate); `/save-web` идемпотентен через content-hash.
10. ✅ **Eval-сет 85 размеченных вопросов** в 7 категориях (включая 8 refusal + 6 privacy + sentinel-кейсы S01-S10) пройден; key metrics: `must_include_recall ≥ 0.85`, `hallucination_rate ≤ 0.05`, `coverage ≥ 0.70`, `faithfulness ≥ 0.90`. Cohen's κ author ↔ LLM-judge ≥ 0.6.
11. ✅ KGE-предсказания доступны в `/review` UI; adaptive surfacing (z-score + per-entity quota + round-robin diversity + skip already-reviewed + re-show на росте score) работает; ≥ 3 training cycles завершены без quality-gate отказов.
12. ✅ **Bootstrap-скрипт `scripts/install.sh`** ставит проект на чистой машине из `git clone` за один прогон с тремя режимами:
    - `--auto` — автоматически выполняет всё: `uv sync`, скачивание моделей по выбранному hardware-профилю (laptop → `Qwen/Qwen3-Embedding-0.6B` + `Qwen/Qwen3-Reranker-0.6B`; desktop-mid/large → `Qwen/Qwen3-Embedding-4B` + `Qwen/Qwen3-Reranker-4B`; FastEmbed Bm25 — лениво при первом upsert'е), `ollama pull qwen3:8b` (или `qwen3:14b` на desktop-large), создание `data/{qdrant,oxigraph,sqlite,books,web-saved,kge,cache,logs}/`, опциональный clone SearXNG в `vendor/`, генерация systemd-юнитов из шаблонов в `infra/systemd/{app,searxng,open-webui}.service`, симлинк в `~/.config/systemd/user/` и `systemctl --user enable --now app searxng open-webui`.
    - `--review` (default) — делает всё то же, кроме симлинков и `enable --now`: финальные unit-файлы и команды печатаются, пользователь самостоятельно проверяет и запускает.
    - `--manual` — только готовит зависимости и data-директории, никаких systemd-операций; печатает копи-пейст команды для ручного `uv run python -m app run`.

    Идемпотентность: повторный запуск любого режима не ломает существующее состояние (skip уже скачанных моделей, regenerate unit-файлов с обновлёнными путями). Парный `scripts/uninstall.sh` (или `--uninstall`) делает `systemctl --user disable --now`, удаляет симлинки и (опц.) `data/`.

13. ✅ Документация: PRD (этот), `docs/architecture.md`, `docs/ontology.ttl` (Turtle), **ADR-0001..0011**, README с инструкциями развёртывания (`git clone` → `scripts/install.sh --review` → проверить unit'ы → `systemctl --user enable --now ...`, без Docker).

---

## 12. Глоссарий

| Термин | Определение |
|--------|-------------|
| **PKM** | Personal Knowledge Management — практика организации личных знаний |
| **PARA** | Methodology Tiago Forte: Projects / Areas / Resources / Archives |
| **Vault** | Хранилище заметок Obsidian (папка с Markdown-файлами) |
| **RDF** | Resource Description Framework — стандарт W3C для описания знаний в виде троек (subject, predicate, object) |
| **SPARQL** | Стандартный язык запросов к RDF-данным (как SQL для реляционных БД) |
| **Triple store** | База данных, оптимизированная для хранения RDF-троек |
| **Named graph** | Подграф внутри triple store с собственным URI; используется для разделения tier-ов (Tier-0/1/2 + predictions) |
| **OpenIE** | Open Information Extraction — извлечение троек из неструктурированного текста |
| **Knowledge Graph** | Граф, где узлы — сущности, рёбра — отношения между ними |
| **KGE** | Knowledge Graph Embedding — методы машинного обучения, представляющие сущности и отношения графа в виде векторов; используются для предсказания недостающих связей |
| **Link prediction** | Задача предсказания возможных связей в графе на основе уже известных |
| **TransE, RotatE, ComplEx, DistMult, TuckER, ConvE** | Конкретные KGE-модели разных семейств; в проекте используется **RotatE** |
| **MRR / Hits@k** | Стандартные метрики качества link prediction; MRR — средний реципрокный ранг |
| **AMR / AMRI** | Adjusted Mean Rank / Adjusted Arithmetic Mean Rank Index — метрики, корректирующие размер графа; критичны для малых персональных графов |
| **Filtered ranking** | Режим оценки, когда из ranked-списка убираются другие корректные сущности при подсчёте ранга целевой; стандарт публикаций |
| **Realistic ranking** | Способ подсчёта ранга при равных score'ах через мат. ожидание |
| **LCWA / sLCWA** | Local Closed World Assumption — режимы тренировки KGE; sLCWA = с негативным семплированием (используется в проекте) |
| **RAG** | Retrieval-Augmented Generation — паттерн генерации ответов LLM с подмешиванием релевантного контекста |
| **Hybrid RAG** | RAG, использующий несколько источников для retrieval (в проекте — vector + sparse + graph + опционально web) |
| **Multi-hop reasoning** | Ответ на вопрос требует прохода через несколько связей в графе или несколько источников |
| **Wiki-link** | В Obsidian — синтаксис `[[название заметки]]` |
| **Wikidata** | Открытая база знаний Wikimedia Foundation; доступна через API и SPARQL endpoint |
| **Embedding** | Векторное представление текста, в котором семантически близкие тексты оказываются рядом |
| **Chunk / чанкинг** | Разбиение длинного документа на короткие фрагменты для векторного индексирования |
| **HippoRAG / GraphRAG / LightRAG** | RAG-фреймворки с графом знаний (источники вдохновения, не зависимости) |
| **ADR** | Architecture Decision Record — документ одного решения: Context / Options / Decision / Consequences |
| **Three-zone grounding** | Дисциплина LLM-генерации: Z1 grounded core (только из retrieved, с citations), Z2 parametric framing (связки без citations), Z3 explicit fallback (явно отделённый parametric блок) |
| **Faithfulness** | Свойство ответа: каждое factual утверждение покрыто citation. Проверка через atomic decomposition + claim ↔ quote + tiered warnings (см. ADR-0007) |
| **Soft routing** | Router выдаёт не один intent, а веса каналов {notes, books, wikidata, web} — устойчивее к ошибкам classification |
| **Sanitization gate** | Удаление приватных маркеров (`[[wiki]]`, `#tag`, sensitive entities) из запроса перед outbound в web |
| **Parent-child chunking** | Retrieval по small chunk (512 tok), context для LLM — parent (секция / вся заметка); promotion до главы при множественных матчах |
| **RRF (Reciprocal Rank Fusion)** | Стратегия объединения нескольких ranked-списков (dense + sparse + graph) без необходимости калибровать score'ы |
| **Cross-encoder reranker** | Модель, которая на парах (query, doc) выдаёт score; запускается на top-K первичного retrieval, +15-30% Recall@10 |
| **Tier-0 / Tier-1 / Tier-2 graph** | Tier-0 = детерминированный (теги, wiki-links, метаданные); Tier-1 = LLM-OpenIE с multi-gate auto-promotion; Tier-2 = Wikidata enrichment (L1 core / L2 type-specific / L3 lazy) |
| **MMR (Maximal Marginal Relevance)** | Стратегия отбора diverse результатов: penalize похожие на уже выбранные (λ=0.6) |
| **HyDE** | Hypothetical Document Embeddings — LLM генерирует псевдо-документ-ответ, эмбеддится, ищем similar (применяется в LLM-fallback ветке query understanding) |
| **Cohen's κ** | Коэффициент согласия двух разметчиков; ≥ 0.6 = «substantial» по Landis & Koch |
| **Sentinel case** | Конкретный качественный тест-кейс, встроенный в eval-сет; failure-сигнал для регрессий (S01-S10) |
| **Trust level / tier** | Уровень доверия источнику или провайдеру; определяет, какие данные допустимо ему отправлять (LLM) и какой score multiplier применить (retrieval source) |
| **OpenTelemetry / MLflow** | Стандарты observability и ML-experiment tracking; вынесены в future work (см. §6.2) |
