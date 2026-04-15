# Схема базы данных

База данных — PostgreSQL. ORM — SQLAlchemy, миграции — Alembic. Все чувствительные данные (API-ключи, токены, секреты вебхуков) хранятся зашифрованными через Fernet.

---

## Таблицы

### `users` — Пользователи

Пользователи системы с разграничением прав доступа.

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | UUID (PK) | Уникальный идентификатор |
| `email` | String(255), UNIQUE | Электронная почта для входа |
| `username` | String(100), UNIQUE | Отображаемое имя |
| `hashed_password` | String(255) | Пароль, захешированный bcrypt |
| `role` | Enum | Роль: `admin`, `reviewer`, `viewer` |
| `is_active` | Boolean | Активен ли аккаунт |
| `created_at` | DateTime | Дата создания |
| `updated_at` | DateTime | Дата последнего изменения |

**Связи:** владеет промптами и задачами на ревью.

---

### `git_servers` — Git-серверы

Конфигурация GitLab-серверов (инстансов).

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | UUID (PK) | Уникальный идентификатор |
| `name` | String(150), UNIQUE | Название сервера |
| `base_url` | String(500) | Базовый URL (напр. `https://gitlab.com`) |
| `access_token_encrypted` | Text | Зашифрованный access-токен GitLab |
| `is_active` | Boolean | Активен ли сервер |
| `created_at` | DateTime | Дата создания |
| `updated_at` | DateTime | Дата последнего изменения |

**Связи:** один сервер содержит много репозиториев. При удалении сервера удаление репозиториев блокируется (RESTRICT).

---

### `repositories` — Репозитории

Репозитории GitLab, подключённые к системе.

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | UUID (PK) | Уникальный идентификатор |
| `git_server_id` | UUID (FK → git_servers) | Сервер, на котором находится репозиторий |
| `gitlab_project_id` | Integer | Внутренний ID проекта в GitLab |
| `name` | String(300) | Название репозитория |
| `webhook_secret_encrypted` | Text | Зашифрованный секрет вебхука |
| `llm_model_id` | UUID (FK → llm_models, nullable) | Модель LLM по умолчанию для репозитория |
| `review_commits` | Boolean | Включить ревью коммитов |
| `review_mrs` | Boolean | Включить ревью merge request'ов |
| `is_active` | Boolean | Активен ли репозиторий |
| `created_at` | DateTime | Дата создания |
| `updated_at` | DateTime | Дата последнего изменения |

**Уникальность:** пара (`git_server_id`, `gitlab_project_id`) уникальна — один проект не может быть добавлен дважды для одного сервера.

---

### `llm_endpoints` — LLM-эндпоинты

Провайдеры языковых моделей (OpenAI, Ollama и др.).

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | UUID (PK) | Уникальный идентификатор |
| `name` | String(100), UNIQUE | Название провайдера (напр. `OpenAI`) |
| `base_url` | String(500) | URL API |
| `api_key_encrypted` | Text, nullable | Зашифрованный API-ключ (может отсутствовать для локальных моделей) |
| `is_active` | Boolean | Активен ли эндпоинт |
| `created_by_id` | UUID (FK → users, nullable) | Пользователь, создавший эндпоинт |
| `created_at` | DateTime | Дата создания |
| `updated_at` | DateTime | Дата последнего изменения |

**Связи:** один эндпоинт содержит много моделей. При удалении эндпоинта все его модели удаляются каскадно.

---

### `llm_models` — LLM-модели

Конкретные модели внутри провайдера.

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | UUID (PK) | Уникальный идентификатор |
| `endpoint_id` | UUID (FK → llm_endpoints, CASCADE) | Провайдер модели |
| `model_name` | String(200) | Идентификатор модели (напр. `gpt-4`, `claude-3-opus`) |
| `display_name` | String(200), nullable | Отображаемое имя для UI |
| `max_context_tokens` | Integer | Размер контекстного окна (по умолчанию 8192) |
| `temperature` | Float | Температура сэмплирования (по умолчанию 0.2) |
| `is_active` | Boolean | Активна ли модель |
| `is_global_default` | Boolean | Глобальная модель по умолчанию |
| `created_at` | DateTime | Дата создания |
| `updated_at` | DateTime | Дата последнего изменения |

**Уникальность:** пара (`endpoint_id`, `model_name`) уникальна.

---

### `prompts` — Промпты

Шаблоны промптов для LLM. Бывают глобальными (user_id = NULL) или принадлежащими пользователю.

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | UUID (PK) | Уникальный идентификатор |
| `user_id` | UUID (FK → users, nullable) | Владелец промпта; NULL = глобальный |
| `prompt_type` | Enum | Тип: `system`, `commit_review`, `mr_review` |
| `name` | String(200) | Название промпта |
| `content` | Text | Содержимое шаблона (может содержать плейсхолдеры) |
| `is_default` | Boolean | Промпт по умолчанию для своего типа |
| `is_active` | Boolean | Активен ли промпт |
| `created_at` | DateTime | Дата создания |
| `updated_at` | DateTime | Дата последнего изменения |

**Порядок разрешения промпта для задачи:**
1. Промпт пользователя с нужным типом
2. Глобальный промпт по умолчанию с нужным типом
3. Если не найден — задача завершается с ошибкой

---

### `review_jobs` — Задачи ревью

Каждая задача соответствует одному запросу на ревью коммита или MR.

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | UUID (PK) | Уникальный идентификатор |
| `repository_id` | UUID (FK → repositories, CASCADE) | Репозиторий |
| `trigger_type` | Enum | Тип триггера: `commit` или `mr` |
| `commit_sha` | String(40), nullable | SHA коммита (для типа `commit`) |
| `mr_iid` | Integer, nullable | Внутренний ID MR в GitLab (для типа `mr`) |
| `status` | Enum | Статус: `pending`, `running`, `completed`, `failed` |
| `triggered_by_id` | UUID (FK → users, nullable) | Пользователь, запустивший задачу; NULL = система |
| `llm_model_id` | UUID (FK → llm_models, nullable) | Модель, использованная для ревью |
| `prompt_id` | UUID (FK → prompts, nullable) | Промпт, использованный для ревью |
| `error_message` | Text, nullable | Сообщение об ошибке при сбое |
| `started_at` | DateTime, nullable | Время начала выполнения |
| `completed_at` | DateTime, nullable | Время завершения |
| `created_at` | DateTime | Дата создания |
| `updated_at` | DateTime | Дата последнего изменения |

**Обработка:** задача выполняется воркером Celery через LangGraph-пайплайн.

---

### `review_comments` — Комментарии ревью

Конкретные замечания, сгенерированные LLM в рамках задачи.

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | UUID (PK) | Уникальный идентификатор |
| `job_id` | UUID (FK → review_jobs, CASCADE) | Родительская задача |
| `file_path` | String(1000), nullable | Путь к файлу |
| `line_number` | Integer, nullable | Начальная строка (или единственная) |
| `line_end` | Integer, nullable | Конечная строка для диапазонных комментариев |
| `comment_body` | Text | Текст комментария |
| `severity` | Enum | Серьёзность: `info`, `warning`, `error` |
| `gitlab_note_id` | String(100), nullable | ID заметки в GitLab после публикации |
| `posted_at` | DateTime, nullable | Время публикации в GitLab |
| `created_at` | DateTime | Дата создания |

---

### `request_logs` — Лог HTTP-запросов

Аудит всех HTTP-запросов к API. Хранит снапшот пользователя без внешнего ключа — выживает при удалении пользователя.

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | UUID (PK) | Уникальный идентификатор |
| `user_id` | UUID (без FK) | Снапшот ID пользователя |
| `username` | String(150), nullable | Снапшот имени пользователя |
| `ip_address` | String(45), nullable | IP-адрес клиента (поддерживает IPv6) |
| `method` | String(10) | HTTP-метод (GET, POST и т.д.) |
| `path` | String(2000) | Путь запроса |
| `status_code` | Integer | Код ответа HTTP |
| `timestamp` | DateTime | Время запроса |

---

### `event_logs` — Лог событий

Структурированный аудит системных событий (входы, завершения ревью и т.д.).

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | UUID (PK) | Уникальный идентификатор |
| `event_type` | String(100) | Тип события (напр. `user.login`, `review.completed`) |
| `level` | String(10) | Уровень: `info`, `warning`, `error` |
| `user_id` | UUID (без FK), nullable | Снапшот ID пользователя |
| `username` | String(150), nullable | Снапшот имени пользователя |
| `ip_address` | String(45), nullable | IP-адрес клиента |
| `message` | Text | Описание события |
| `details` | JSONB, nullable | Структурированные данные события |
| `timestamp` | DateTime | Время события |

---

## Перечисления (Enum)

| Имя | Значения |
|-----|----------|
| `user_role_enum` | `admin`, `reviewer`, `viewer` |
| `prompt_type_enum` | `system`, `commit_review`, `mr_review` |
| `trigger_type_enum` | `commit`, `mr` |
| `review_status_enum` | `pending`, `running`, `completed`, `failed` |
| `severity_enum` | `info`, `warning`, `error` |

---

## Внешние ключи и поведение при удалении

| Таблица.колонка | Ссылается на | При удалении |
|-----------------|--------------|--------------|
| `repositories.git_server_id` | `git_servers.id` | RESTRICT (блокирует удаление сервера) |
| `repositories.llm_model_id` | `llm_models.id` | SET NULL |
| `llm_endpoints.created_by_id` | `users.id` | SET NULL |
| `llm_models.endpoint_id` | `llm_endpoints.id` | CASCADE |
| `prompts.user_id` | `users.id` | CASCADE |
| `review_jobs.repository_id` | `repositories.id` | CASCADE |
| `review_jobs.triggered_by_id` | `users.id` | SET NULL |
| `review_jobs.llm_model_id` | `llm_models.id` | SET NULL |
| `review_jobs.prompt_id` | `prompts.id` | SET NULL |
| `review_comments.job_id` | `review_jobs.id` | CASCADE |

---

## История миграций

| Файл | Что изменилось |
|------|----------------|
| `0001_initial_schema.py` | Создание всех основных таблиц |
| `0002_seed_default_prompts.py` | Добавление промптов по умолчанию |
| `0003_split_git_servers.py` | Вынос git_servers в отдельную таблицу |
| `0004_add_review_comment_line_end.py` | Добавление колонки `line_end` для диапазонных комментариев |
| `0005_localize_default_prompts_to_russian.py` | Промпты переведены на русский |
| `0006_prefer_line_ranges_in_default_prompts.py` | Уточнение формата диапазонов строк в промптах |
| `0007_add_request_logs.py` | Создание таблицы `request_logs` |
| `0008_add_event_logs.py` | Создание таблицы `event_logs` |
