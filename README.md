# DocFlow AI — Phase 1 Scaffold

Implements Phase 1: upload flow & tagging, document identity & versioning,
multi-tenant storage schema, file validation, and the ingestion -> Structure
Scanner async handoff.

## Setup (Windows + VS Code)

1. **Open the folder in VS Code**: File -> Open Folder -> select this `docflow-ai` folder.

2. **Create a virtual environment** (open a terminal in VS Code: Terminal -> New Terminal):
   ```
   python -m venv venv
   venv\Scripts\activate
   ```
   Your terminal prompt should now show `(venv)` at the start.

3. **Install dependencies:**
   ```
   pip install -r requirements.txt
   ```

4. **Create your `.env` file:**
   - Copy `.env.example` to a new file named `.env` (same folder).
   - Edit `.env` and replace `<your_password>` with the Postgres password you set during install.
   - Make sure the database name matches what you created in pgAdmin (`docflow_ai`).

5. **Run the first migration** to create all tables in Postgres:
   ```
   alembic revision --autogenerate -m "Phase 1 initial schema"
   alembic upgrade head
   ```
   - The first command generates a migration file under `alembic/versions/` based on the models.
   - The second command actually applies it, creating the tables in your `docflow_ai` database.
   - Open pgAdmin afterward and refresh — you should see all the tables (`tenants`, `projects`, `documents`, `document_versions`, etc.) now present.

6. **Start the FastAPI app:**
   ```
   uvicorn app.main:app --reload
   ```
   - Visit `http://localhost:8000/docs` in your browser — this gives you an interactive UI to test the `/documents/upload` endpoint directly, without needing a frontend yet.
   - Visit `http://localhost:8000/health` to confirm the app is running (`{"status": "ok"}`).

7. **(Optional, for later) Start the async polling worker** in a second terminal (activate the venv there too):
   ```
   python -m app.workers.structure_scanner_worker
   ```
   This currently just prints a placeholder message for any document sitting in `pending_review` — Phase 3 will replace the placeholder with real Structure Scanner logic.

## Testing the upload endpoint manually

Since there's no `tenants`/`projects`/`teams`/`users` data yet (no admin UI built yet), you'll need to insert a few rows manually via pgAdmin's Query Tool to test an upload end-to-end:

```sql
INSERT INTO tenants (tenant_id, name, created_at) VALUES (gen_random_uuid(), 'Test Company', now());
-- copy the generated tenant_id, then:
INSERT INTO users (user_id, tenant_id, email, is_org_admin, created_at)
  VALUES (gen_random_uuid(), '<tenant_id>', 'test@example.com', false, now());
INSERT INTO projects (project_id, tenant_id, name, created_at)
  VALUES (gen_random_uuid(), '<tenant_id>', 'Test Project', now());
INSERT INTO teams (team_id, project_id, name)
  VALUES (gen_random_uuid(), '<project_id>', 'Engineering');
INSERT INTO user_team_memberships (id, user_id, team_id, project_id, role)
  VALUES (gen_random_uuid(), '<user_id>', '<team_id>', '<project_id>', 'contributor');
INSERT INTO stages (stage_id, project_id, name, order_index, created_at)
  VALUES (gen_random_uuid(), '<project_id>', 'Requirements', 1, now());
```

> Note: `gen_random_uuid()` requires the `pgcrypto` extension. If it errors, run
> `CREATE EXTENSION IF NOT EXISTS pgcrypto;` once in the Query Tool first.

Then in `/docs`, try the `POST /documents/upload` endpoint with the IDs you just created, plus a small PDF/DOCX/TXT/MD file.

## What's implemented vs. deferred

- ✅ Upload flow with project/stage mandatory, team-visibility/sensitivity defaults
- ✅ Document identity (`documents`) + versioning (`document_versions`) schema
- ✅ Multi-tenant schema with denormalized `tenant_id` on documents
- ✅ File type/size validation (PDF/DOCX/TXT/MD, 10MB cap)
- ✅ Async polling worker skeleton (handoff contract only — scanner logic is Phase 3)
- ⏳ Auth (currently `uploaded_by`/`tenant_id` are passed as raw form fields — will be replaced with real auth-derived values once an auth provider is chosen)
- ⏳ "Upload new version" endpoint (only "new document" upload exists so far)
- ⏳ Structure Scanner itself (Phase 3)
