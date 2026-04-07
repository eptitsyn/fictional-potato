# AI Code Reviewer — Codex Context

## Project Purpose
Automatically reviews GitLab commits and merge requests using configurable LLM backends.
Posts inline comments back to GitLab. All configuration is done via a web UI.

## Stack
- **FastAPI** — async HTTP API + serves single-page UI at `/`
- **SQLAlchemy async** + **asyncpg** — PostgreSQL ORM
- **Alembic** — DB migrations (run automatically on `docker compose up`)
- **LangGraph** — multi-step review agent (plan → security → quality → consolidate)
- **LangChain + langchain-openai** — OpenAI-compatible LLM connector
- **Celery + Redis** — async review job queue (queue name: `reviews`)
- **Flower** — Celery monitoring UI at `:5555`

## Key Conventions

### Secrets
All secrets (LLM API keys, GitLab tokens, webhook secrets) are **Fernet-encrypted at rest**.
Encryption key is HKDF-derived from `SECRET_KEY` env var.
- Encrypt: `app.core.security.encrypt(plain_str)`
- Decrypt: `app.core.security.decrypt(ciphertext_str)`
- Never include encrypted fields in Pydantic read schemas — write-only only

### RBAC
Three roles: `admin`, `reviewer`, `viewer`.
Enforced via `app.dependencies`:
```python
from app.dependencies import AdminUser, ReviewerUser, CurrentUser, DB
```
- `AdminUser` — only admins
- `ReviewerUser` — admins + reviewers
- `CurrentUser` — any authenticated user

### Prompt Cascade
`prompt_service.resolve_effective_prompt(db, user_id, prompt_type)`:
1. User-specific prompt (by `user_id` + `prompt_type`)
2. Global default (`user_id IS NULL`, `is_default=True`)
3. Returns `None` (task will raise RuntimeError, job → failed)

### LangGraph Review Pipeline
`app/langchain_integration/review_graph.py` — 3 LLM calls per diff chunk:
1. `plan_review` — identifies files and focus areas
2. `security_review` — OWASP-focused scan
3. `quality_review` — bugs, perf, maintainability
4. `consolidate` — deduplicates and merges comments

Falls back to single-shot (`chains.py`) on any graph error.

### Adding a New API Route
1. Create `app/api/v1/my_feature.py` with an `APIRouter`
2. Import + include in `app/api/v1/router.py`
3. Add corresponding service in `app/services/my_feature_service.py`
4. Add Pydantic schemas in `app/schemas/my_feature.py`
5. Add SQLAlchemy model in `app/models/my_feature.py` + Alembic migration

### Running Locally (Docker)
```bash
docker compose up
```
Services:
- `app` → http://localhost:8000 (API + UI)
- `worker` → Celery worker processing review jobs
- `flower` → http://localhost:5555 (Celery monitor)
- `db` → PostgreSQL on :5432
- `redis` → Redis on :6379

Default credentials: `admin` / `changeme123`

### Running Without Docker
```bash
# Requires postgres + redis running locally
pip install -e .
alembic upgrade head
uvicorn app.main:app --reload  # terminal 1
celery -A app.workers.celery_app worker -Q reviews --loglevel=info  # terminal 2
```

## Directory Layout
```
app/
  main.py                    # FastAPI app, lifespan bootstrap
  config.py                  # Pydantic Settings (reads .env)
  dependencies.py            # FastAPI Depends: DB, RBAC, CurrentUser
  core/
    database.py              # async engine, AsyncSessionLocal, Base
    security.py              # Fernet encrypt/decrypt, JWT, bcrypt
    exceptions.py            # HTTPException subclasses
  models/                    # SQLAlchemy ORM models
  schemas/                   # Pydantic v2 request/response schemas
  services/                  # Business logic (no HTTP concerns)
  api/v1/                    # FastAPI routers
  langchain_integration/
    chains.py                # Single-shot review chain (fallback)
    review_graph.py          # LangGraph multi-step pipeline
  workers/
    celery_app.py            # Celery app config
    tasks.py                 # run_review_job Celery task
  ui/templates/index.html    # Single-page HTML/JS UI
alembic/
  versions/0001_*            # Schema creation
  versions/0002_*            # Default prompt seeds
```

## Important Env Vars
| Var | Purpose |
|---|---|
| `SECRET_KEY` | JWT signing + Fernet key derivation (change in prod!) |
| `ENCRYPTION_SALT` | HKDF salt for Fernet key (change in prod!) |
| `DATABASE_URL` | asyncpg connection string |
| `REDIS_URL` | Celery broker/backend |
| `FIRST_ADMIN_*` | Bootstrapped admin on first startup |

## GitLab Webhook Setup
After adding a repository in the UI:
1. Go to GitLab project → Settings → Webhooks
2. URL: `http://your-host:8000/api/v1/webhooks/gitlab/{repository_id}`
3. Secret Token: the `webhook_secret` you entered when adding the repo
4. Enable: Push events, Merge request events
