# realtornet-prod (Meta Global AI Developer Hackathon)

An agency-first property marketplace for Nigeria, integrated with the **Meta Model API (`muse-spark-1.3`)** to assist human administrators, agency owners, and agents with secure moderation drafting and inquiry replies, while enforcing strict human-in-the-loop governance.

---

## Architecture & Overview

* **Backend (`FastAPI`):** Custom AI assist service (`app/services/ai_assist/`) and router (`app/api/endpoints/ai_assist.py`). Handles listing/inquiry payload loading server-side, rate-limiting (10 req/min), prompt isolation via untrusted markdown delimiters (`<<<UNTRUSTED_*>>>`), and fixed 502/503 error handling.
* **Frontend (`Next.js / React`):** Isolated feature module (`features/ai-assist/`) containing TanStack Query hooks, `SuggestReasonButton`, `SuggestReplyButton`, and a pinnable shadcn `Sheet` debug drawer showing live model IDs, endpoint hosts, and request IDs.
* **Meta Model Integration:** Powered by `muse-spark-1.3` via `https://api.meta.ai/v1`.

---

## Pre-Existing Work Disclosure
In accordance with hackathon rules, this submission isolates brand-new features built during the event:
* **Pre-existing Codebase (Developed since March 2026):** RealtorNet core marketplace platform, user authentication, multi-tenant row-level security, and core listing lifecycle state machines.
* **Hackathon Event Work (Oct 26 – Nov 4, 2026):** The complete `ai-assist` package (`features/ai-assist/`, `app/services/ai_assist/`, API endpoints, unit tests, and live metadata debug drawer).

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
META_MODEL_BASE_URL=[https://api.meta.ai/v1](https://api.meta.ai/v1)

```


3. Run backend tests:
```bash
pytest tests/test_ai_assist.py

```
