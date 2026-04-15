# План системы логирования

## Текущее состояние

### Что уже есть

**Таблицы в БД:**
- `request_logs` — каждый HTTP-запрос к API (100% покрытие через middleware)
- `event_logs` — доменные события (частичное покрытие)

**Уже логируется:**
- Аутентификация: вход (успех/провал), выход
- Вебхуки GitLab: получение, невалидный токен, игнорирование
- Выполнение ревью в воркере: старт, LLM-обмены, публикация комментариев, завершение/ошибка

**Не логируется (критические пробелы):**
- CRUD пользователей, git-серверов, репозиториев, LLM-эндпоинтов, моделей, промптов
- Ручные действия над задачами ревью (запуск, повтор, удаление)
- Python-логи (`logger.error()` и т.д.) не попадают в `event_logs`, только в stdout
- Нет централизованной конфигурации логирования

---

## Архитектура

### Два уровня логирования

```
Действие → EventLog (БД, structured, queryable)
           Python logger → stdout (JSON, для агрегаторов: Loki, ELK, CloudWatch)
```

Оба уровня должны работать параллельно. `EventLog` — для аудита и UI. Python logger — для ops-мониторинга.

### Конфигурация Python-логирования

Добавить в `app/core/logging_config.py`:

```python
import logging
import json

class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "exc": self.formatException(record.exc_info) if record.exc_info else None,
        })

def configure_logging(log_level: str = "INFO"):
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=log_level, handlers=[handler], force=True)
```

Вызывать при старте FastAPI (`lifespan`) и при старте Celery-воркера.

---

## Полный реестр событий

### Формат записи

Каждое событие пишется через `log_event()` из `app/services/event_log_service.py` и имеет:
- `event_type` — строка в формате `domain.action` (индексируется)
- `level` — `info` / `warning` / `error`
- `user_id`, `username`, `ip_address` — из контекста запроса или задачи
- `message` — человекочитаемая строка
- `details` — JSONB с конкретными данными события

---

### Аутентификация

| event_type | level | Где писать | details |
|---|---|---|---|
| `auth.login.success` | info | `api/v1/auth.py` | `{role, user_id}` |
| `auth.login.failed` | warning | `api/v1/auth.py` | `{attempted_username}` |
| `auth.logout` | info | `api/v1/auth.py` | — |
| `auth.password_changed` | info | `services/user_service.py` | `{user_id}` |

**Статус:** `auth.login.*` и `auth.logout` уже реализованы. Добавить `auth.password_changed`.

---

### Управление пользователями *(не реализовано)*

| event_type | level | Где писать | details |
|---|---|---|---|
| `user.created` | info | `services/user_service.py` | `{new_user_id, new_username, role}` |
| `user.updated` | info | `services/user_service.py` | `{target_user_id, changed_fields: [...]}` |
| `user.deactivated` | warning | `services/user_service.py` | `{target_user_id, target_username}` |
| `user.deleted` | warning | `services/user_service.py` | `{target_user_id, target_username}` |
| `user.role_changed` | warning | `services/user_service.py` | `{target_user_id, old_role, new_role}` |

> Изменение роли и деактивация — `warning`, так как это привилегированные действия с последствиями для безопасности.

---

### Git-серверы *(не реализовано)*

| event_type | level | Где писать | details |
|---|---|---|---|
| `git_server.created` | info | `services/git_server_service.py` | `{server_id, name, base_url}` |
| `git_server.updated` | info | `services/git_server_service.py` | `{server_id, changed_fields: [...]}` |
| `git_server.deleted` | warning | `services/git_server_service.py` | `{server_id, name}` |
| `git_server.token_rotated` | info | `services/git_server_service.py` | `{server_id, name}` |

---

### Репозитории *(не реализовано)*

| event_type | level | Где писать | details |
|---|---|---|---|
| `repository.created` | info | `services/repository_service.py` | `{repo_id, name, git_server_id, gitlab_project_id}` |
| `repository.updated` | info | `services/repository_service.py` | `{repo_id, name, changed_fields: [...]}` |
| `repository.deleted` | warning | `services/repository_service.py` | `{repo_id, name}` |
| `repository.deactivated` | info | `services/repository_service.py` | `{repo_id, name}` |
| `repository.webhook_secret_rotated` | info | `services/repository_service.py` | `{repo_id, name}` |

---

### LLM-эндпоинты и модели *(не реализовано)*

| event_type | level | Где писать | details |
|---|---|---|---|
| `llm_endpoint.created` | info | `services/llm_endpoint_service.py` | `{endpoint_id, name, base_url}` |
| `llm_endpoint.updated` | info | `services/llm_endpoint_service.py` | `{endpoint_id, changed_fields: [...]}` |
| `llm_endpoint.deleted` | warning | `services/llm_endpoint_service.py` | `{endpoint_id, name}` |
| `llm_endpoint.tested` | info | `services/llm_endpoint_service.py` | `{endpoint_id, success: bool, latency_ms}` |
| `llm_model.created` | info | `services/llm_endpoint_service.py` | `{model_id, model_name, endpoint_id}` |
| `llm_model.updated` | info | `services/llm_endpoint_service.py` | `{model_id, changed_fields: [...]}` |
| `llm_model.deleted` | warning | `services/llm_endpoint_service.py` | `{model_id, model_name}` |
| `llm_model.set_global_default` | info | `services/llm_endpoint_service.py` | `{model_id, model_name, prev_default_id}` |

---

### Промпты *(не реализовано)*

| event_type | level | Где писать | details |
|---|---|---|---|
| `prompt.created` | info | `services/prompt_service.py` | `{prompt_id, name, prompt_type, is_global: bool}` |
| `prompt.updated` | info | `services/prompt_service.py` | `{prompt_id, name, changed_fields: [...]}` |
| `prompt.deleted` | warning | `services/prompt_service.py` | `{prompt_id, name}` |
| `prompt.set_default` | info | `services/prompt_service.py` | `{prompt_id, name, prompt_type, prev_default_id}` |

---

### Задачи ревью

| event_type | level | Где писать | details | Статус |
|---|---|---|---|---|
| `review.triggered_manual` | info | `api/v1/reviews.py` | `{job_id, repo_id, trigger_type, commit_sha or mr_iid}` | ❌ не реализовано |
| `review.retried` | info | `api/v1/reviews.py` | `{job_id, old_status}` | ❌ не реализовано |
| `review.restarted` | info | `api/v1/reviews.py` | `{job_id, deleted_comments_count}` | ❌ не реализовано |
| `review.deleted` | warning | `api/v1/reviews.py` | `{job_id, status_at_deletion}` | ❌ не реализовано |
| `review.started` | info | `workers/tasks.py` | `{job_id, repo, trigger_type, commit_sha or mr_iid}` | ✅ реализовано |
| `review.completed` | info | `workers/tasks.py` | `{job_id, comment_count, model_used}` | ✅ реализовано |
| `review.failed` | error | `workers/tasks.py` | `{job_id, error}` | ✅ реализовано |
| `review.timeout` | error | `workers/tasks.py` | `{job_id, elapsed_seconds}` | ❌ не реализовано |

---

### Вебхуки GitLab

| event_type | level | Где писать | details | Статус |
|---|---|---|---|---|
| `gitlab.webhook_received` (push) | info | `api/v1/webhooks.py` | `{repo_id, commit_count, job_ids}` | ✅ реализовано |
| `gitlab.webhook_received` (MR) | info | `api/v1/webhooks.py` | `{repo_id, mr_iid, action, job_id}` | ✅ реализовано |
| `gitlab.webhook_invalid_token` | warning | `api/v1/webhooks.py` | `{repo_name, ip}` | ✅ реализовано |
| `gitlab.webhook_ignored` | info | `api/v1/webhooks.py` | `{event_type}` | ✅ реализовано |
| `gitlab.comment_posted` | info | `workers/tasks.py` | `{job_id, file_path, line_number, severity, gitlab_note_id}` | ✅ реализовано |
| `gitlab.comment_failed` | error | `workers/tasks.py` | `{job_id, file_path, error}` | ✅ реализовано |

---

### LLM-пайплайн (воркер)

| event_type | level | Где писать | details | Статус |
|---|---|---|---|---|
| `llm.exchange` | info | `langchain_integration/review_graph.py` | `{stage, model, prompt_chars, response_chars}` | ✅ реализовано |
| `agent.plan` | info | `langchain_integration/review_graph.py` | `{job_id, plan_preview}` | ✅ реализовано |
| `agent.act_iter_N` | info | `langchain_integration/review_graph.py` | `{iteration, tool_calls}` | ✅ реализовано |
| `agent.reflect` | info | `langchain_integration/review_graph.py` | `{job_id}` | ✅ реализовано |
| `agent.tool_error` | error | `langchain_integration/review_graph.py` | `{tool, error}` | ✅ реализовано |
| `agent.finished` | info | `langchain_integration/review_graph.py` | `{job_id, total_iterations}` | ✅ реализовано |

---

### Системные события

| event_type | level | Где писать | details |
|---|---|---|---|
| `system.startup` | info | `app/main.py` (lifespan) | `{version, first_admin_created: bool}` |
| `system.first_admin_created` | warning | `app/main.py` (lifespan) | `{username, email}` |
| `celery.worker_started` | info | `workers/celery_app.py` (worker_init signal) | `{hostname, concurrency}` |
| `celery.task_retry` | warning | `workers/tasks.py` | `{job_id, attempt, reason}` |

---

## План реализации

### Этап 1 — Конфигурация логирования (без изменений в БД)

1. Создать `app/core/logging_config.py` с JSON-форматтером
2. Вызывать `configure_logging()` в `lifespan` FastAPI и в `celery_app.py`
3. Заменить `print()` вызовы (если есть) на `logger.*`

### Этап 2 — Логи CRUD-операций (наибольший приоритет)

Добавить вызовы `log_event()` в сервисы. Паттерн одинаковый для всех:

```python
# После успешного сохранения в БД
await log_event(
    db=db,
    event_type="user.created",
    level="info",
    user_id=str(current_user.id),      # кто выполнил действие
    username=current_user.username,
    ip_address=request.state.ip,
    message=f"User '{new_user.username}' created",
    details={"new_user_id": str(new_user.id), "role": new_user.role},
)
```

Файлы для правки:
- `app/services/user_service.py`
- `app/services/git_server_service.py`
- `app/services/repository_service.py`
- `app/services/llm_endpoint_service.py`
- `app/services/prompt_service.py`

### Этап 3 — Логи действий над задачами ревью

Добавить в `app/api/v1/reviews.py` (эндпоинты trigger, retry, restart, delete).

### Этап 4 — Системные события

Добавить `system.startup` и `celery.worker_started`. Добавить обработку `review.timeout` через Celery soft time limit.

### Этап 5 — Пробросить Python-логи в EventLog (опционально)

Создать кастомный `logging.Handler`, который пишет записи уровня `WARNING` и выше в таблицу `event_logs`. Подключить в конфигурации логирования.

```python
class EventLogHandler(logging.Handler):
    """Пишет WARNING+ логи в event_logs для queryability."""
    def emit(self, record):
        # async-safe через asyncio.create_task или sync через отдельную сессию
        ...
```

---

## Что НЕ логировать

- Чтение данных (GET-запросы) — уже покрыты `request_logs`, дублировать в `event_logs` нет смысла
- Содержимое промптов и LLM-ответов целиком — только размеры символов (privacy + объём)
- Зашифрованные значения (токены, ключи) — никогда не писать в логи даже encrypted
- Health-check запросы (`/health`, `/`) — уже отфильтрованы middleware

---

## Покрытие после реализации

| Область | Сейчас | После |
|---|---|---|
| HTTP-запросы | ✅ 100% | ✅ 100% |
| Аутентификация | ✅ 100% | ✅ 100% |
| Вебхуки GitLab | ✅ 100% | ✅ 100% |
| Выполнение ревью (воркер) | ✅ ~80% | ✅ 100% |
| Ручные действия над ревью | ❌ 0% | ✅ 100% |
| CRUD конфигураций | ❌ 0% | ✅ 100% |
| CRUD пользователей | ❌ 0% | ✅ 100% |
| Системные события | ❌ 0% | ✅ 100% |
| Python-логи в БД | ❌ 0% | ⚠️ опционально (этап 5) |
