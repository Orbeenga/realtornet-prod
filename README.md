# realtornet-prod (Meta Global AI Developer Hackathon)

An agency-first property marketplace for Nigeria, integrated with the **Meta Model API (`muse-spark-1.3`)** to assist human administrators, agency owners, and agents with secure moderation drafting and inquiry replies, while enforcing strict human-in-the-loop governance.

---

## Architecture & Overview

* **Backend (`FastAPI`):** Custom AI assist service (`app/services/ai_assist_service.py`) and router (`app/api/endpoints/ai_assist.py`). Handles listing/inquiry payload loading server-side, rate-limiting (10 req/min), prompt isolation via untrusted markdown delimiters (`<<<UNTRUSTED_*>>>`), and fixed 502/503 error handling.
* **Frontend (`Next.js / React`):** Isolated feature module (`features/ai-assist/`) containing TanStack Query hooks, `SuggestReasonButton`, `SuggestReplyButton`, and a pinnable shadcn `Sheet` debug drawer showing live model IDs, endpoint hosts, and request IDs.
* **Meta Model Integration:** Powered by `muse-spark-1.3` via `https://api.meta.ai/v1`.
* **Key handling:** The backend reads `META_MODEL_API_KEY` from the environment at runtime. Keys are never committed. `.env.example` documents variable names only, with an empty value.
* **Model hosting:** The model runs hosted by Meta at `META_MODEL_BASE_URL`. Only non-PII listing and inquiry context is sent. Seeker contact details never leave the server.

---

## Pre-Existing Work Disclosure
In accordance with hackathon rules, this submission isolates brand-new features built during the event:
* **Pre-existing Codebase (Developed since March 2026):** RealtorNet core marketplace platform, user authentication, multi-tenant row-level security, and core listing lifecycle state machines.
* **Hackathon Event Work (Oct 26 – Nov 4, 2026):** The complete `ai-assist` package (`features/ai-assist/`, `app/services/ai_assist_service.py`, API endpoints, unit tests, and live metadata debug drawer).

---

## Limitations

* This snapshot is not runnable on its own. The backend files import core application modules, and the frontend module needs its host app (`apiClient`, UI kit, TanStack Query provider).
* Runnable slice: the backend unit tests, run from the full backend repo against its test database: `pytest tests/api/endpoints/test_ai_assist.py`.
* Real provider unverified at submission time: the provider call path was exercised against a stub, so confirm the response shape against the live Meta API before staging.
* Guardrails are fixed: 300 max tokens, 10 requests per minute per user, 15 second timeout. Call logs live in memory (last 100, admin plus demo flag only) and do not persist.

---

## Setup & Configuration

1. Copy environment variables from the template:
   ```bash
   cp .env.example .env
   ```

2. Set your Meta Model API credentials in `.env`:
```env
META_MODEL_API_KEY=your_meta_model_api_key_here
META_MODEL_ID=muse-spark-1.3
META_MODEL_BASE_URL=https://api.meta.ai/v1

```


3. Run backend tests:
```bash
pytest tests/api/endpoints/test_ai_assist.py

```
