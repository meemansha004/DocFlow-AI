# DocFlow AI — frontend

The teammate's React + Vite SPA (`../docflow-app-teammate`), kept intact:
his full nav, page set, routing, and component library. Only `src/lib/api.js`,
`src/context/AuthContext.jsx`, and a few small spots are rewired to this repo's
Phase 1–5 backend.

## What's actually wired to a backend

| Screen | Backend |
|---|---|
| Login / Sign up / Google | `POST /auth/login`, `POST /auth/signup`, `GET /auth/google/authorize` |
| Projects list (`/`) | `GET /workspace` (mapped to his project-card shape) |
| Project Workspace → Sources panel (`/projects/:id`) | `GET /documents?project_id=` + workflow badges + Submit / Approve / Reject |
| Upload (`/projects/:id/upload`) | `GET /workspace` + `POST /documents/upload` (Markdown `content`, explicit team) |
| Admin → Pending Approvals tab | `GET /documents` filtered to `pending_review` + approve/reject |

Everything else (project create, document versions/delete/download, Studio,
Query/RAG agents, Chat, Notes, most of Admin, notifications, password reset,
super-admin) renders in his exact layout but its data section shows
**"This feature isn't connected to the backend yet."** — never a blank screen.

## Run it (two terminals)

**Backend** (repo root):

```
$env:SESSION_TOKEN_SECRET="dev-secret"
$env:DEFAULT_SIGNUP_TENANT_ID="<a real tenants.tenant_id UUID>"
$env:FRONTEND_URL="http://localhost:5173"
venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Seeded users: run `venv\Scripts\python.exe scripts\reset_test_passwords.py`
once (sets every seeded user to `docflow-test-pw`).

**Frontend:**

```
cd frontend
npm install
copy .env.example .env      # VITE_API_BASE=http://localhost:8000
npm run dev                 # http://localhost:5173
```

Sign in as `carol@test.com` / `docflow-test-pw` (contributor) or
`erin@test.com` (team_lead on Engineering, can approve).

## Notes

- Only the "Testing" stage in "Project A" has `requires_approval=True`, so that's
  where the approval workflow is visible. Revert with
  `UPDATE stages SET requires_approval=false WHERE name='Testing';`
- Google sign-in needs `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` on the backend
  and `http://localhost:8000/auth/google/callback` as an authorized redirect URI;
  otherwise the button surfaces a clean 503.
