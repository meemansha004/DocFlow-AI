// Central client for the DocFlow AI backend (FastAPI on :8000).
//
// Phase 6: the teammate's full frontend is kept as-is. This client wires the
// calls it makes to OUR backend:
//   - REAL   : auth (login/signup/me/google), /workspace, /documents (list +
//              upload), the approval workflow (submit/approve/reject), and a
//              real-data mapping for projectsApi.list / .documents /
//              .pendingApprovals so his Projects + Project Workspace screens
//              show live data in his own layout.
//   - STUBBED: everything with no backend yet (projectsApi.create/activity,
//              document versions/delete, ragApi, chatApi, agentsApi, studioApi,
//              notesApi, adminApi, notificationsApi, password reset,
//              super-admin). Every stub rejects with a clear
//              "not connected yet" ApiError so the calling screen shows a
//              message in that section instead of failing silently.

export const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

const TOKEN_KEY = 'docflow_token';

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

export const NOT_CONNECTED_MESSAGE =
  "This feature isn't connected to the backend yet.";

function notConnected(feature) {
  return Promise.reject(
    new ApiError(
      `${NOT_CONNECTED_MESSAGE}${feature ? ` (${feature})` : ''}`,
      501,
      { not_connected: true, feature },
    ),
  );
}

async function request(path, { method = 'GET', body, auth = true, credentials } = {}) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth) {
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    credentials,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  let payload = null;
  const text = await response.text();
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = text; }
  }
  if (!response.ok) {
    let detail = response.statusText || 'Request failed';
    if (payload && typeof payload.detail === 'string') detail = payload.detail;
    else if (payload && Array.isArray(payload.detail) && payload.detail[0]?.msg) {
      detail = payload.detail[0].msg;
    }
    throw new ApiError(detail, response.status, payload);
  }
  return payload;
}

const SENSITIVITY_TO_INT = { public: 0, internal: 1, confidential: 2 };

// ---------- Auth ----------
export const authApi = {
  login: (email, password) =>
    request('/auth/login', { method: 'POST', body: { email, password }, auth: false }),
  // his LoginPage calls signup(formObject); AuthContext narrows it to email+password
  signup: (data) =>
    request('/auth/signup', {
      method: 'POST', auth: false,
      body: { email: data.email, password: data.password },
    }),
  me: () => request('/auth/me'),
  googleAuthorize: () =>
    request('/auth/google/authorize', { auth: false, credentials: 'include' }),

  // no backend for these:
  forgotPassword: () => notConnected('password reset'),
  resetPassword: () => notConnected('password reset'),
  changePassword: () => notConnected('change password'),
  superAdmin: () => notConnected('super-admin login'),
  demo: () => notConnected('demo login'),
};

// ---------- Workspace helper (project/team/stage names + ids) ----------
export const workspaceApi = {
  get: () => request('/workspace'),
};

let _workspaceCache = null;
async function workspace() {
  if (!_workspaceCache) _workspaceCache = await workspaceApi.get();
  return _workspaceCache;
}
export function clearWorkspaceCache() { _workspaceCache = null; }

// Map our GET /documents rows into the shape his components expect.
function mapDocs(rows, project) {
  const stageName = (id) => project?.stages.find((s) => s.stage_id === id)?.name || 'Unspecified';
  const teamName = (id) => project?.teams?.find((t) => t.team_id === id)?.name || null;
  return rows.map((d) => ({
    document_id: d.document_id,
    filename: d.original_filename,
    doc_type: (d.original_filename || '').replace(/\.md$/i, '') || 'Document',
    stage: stageName(d.stage_id),
    stage_id: d.stage_id,
    sensitivity_level: SENSITIVITY_TO_INT[d.sensitivity_level] ?? 1,
    workflow_state: d.workflow_state,
    uploaded_as_team_id: d.uploaded_as_team_id,
    team_name: teamName(d.uploaded_as_team_id),
    project_id: project?.project_id || null,
    created_at: null,
    members: undefined,
  }));
}

// ---------- Projects (his shape, real backend) ----------
export const projectsApi = {
  // GET /projects returns his ProjectSummary shape directly.
  list: () => request('/projects'),
  // his ProjectsPage sends { project_id (slug), project_name, description };
  // our backend takes { name, description } and mints its own UUID.
  create: async (data) => {
    const created = await request('/projects', {
      method: 'POST',
      body: { name: data.project_name || data.name, description: data.description || null },
    });
    clearWorkspaceCache();
    return created;
  },
  documents: async (projectId) => {
    const ws = await workspace();
    const project = ws.projects.find((p) => p.project_id === projectId);
    const rows = await request(`/documents?project_id=${encodeURIComponent(projectId)}`);
    return mapDocs(rows, project);
  },
  pendingApprovals: async (projectId) => {
    const docs = await projectsApi.documents(projectId);
    return docs.filter((d) => d.workflow_state === 'pending_review');
  },
  activity: () => notConnected('project activity'),
};

// ---------- Documents ----------
export const documentsApi = {
  list: (projectId) => projectsApi.documents(projectId),
  listAll: () => notConnected('list all documents'),
  // body: { document_type, stage_id, content, team_id, sensitivity_level }
  upload: (data) => request('/documents/upload', { method: 'POST', body: data }),
  uploadBatch: () => notConnected('batch upload'),
  versions: () => notConnected('document versions'),
  uploadVersion: () => notConnected('upload new version'),
  remove: () => notConnected('delete document'),
  openUrl: () => '#', // no download endpoint yet

  submit: (documentId) =>
    request(`/documents/${encodeURIComponent(documentId)}/submit`, { method: 'POST' }),
  approve: (documentId) =>
    request(`/documents/${encodeURIComponent(documentId)}/approve`, { method: 'POST' }),
  reject: (documentId, reason) =>
    request(`/documents/${encodeURIComponent(documentId)}/reject`, {
      method: 'POST', body: { reason },
    }),
  status: (documentId) => request(`/documents/${encodeURIComponent(documentId)}/status`),
};

// ---------- Not connected yet ----------
const stub = (feature) => new Proxy({}, {
  get: () => () => notConnected(feature),
});

function stubMethods(feature, names) {
  return Object.fromEntries(names.map((n) => [n, () => notConnected(feature)]));
}

// ---------- Admin: real for the directory / invite / audit surface ----------
export const adminApi = {
  // -> [{ user_id, username, full_name, team_name, is_org_admin, roles:[{project_id, role}] }]
  listUsers: () => request('/admin/users'),
  // his form collects full_name/team_name too; our backend only needs the email.
  createUser: (data) => request('/admin/users', {
    method: 'POST',
    body: { email: data.email, full_name: data.full_name || null },
  }),
  // his "Promote to org admin" button (role is always 'admin' from his UI)
  assignAccess: (data) => request('/admin/access', { method: 'POST', body: { email: data.email } }),
  // invite / assign a TeamRole on a specific team. Accepts his
  // { email, project_id, role, team_name } — backend resolves team_name -> team_id
  // and maps role 'member' -> contributor (rejects 'admin' with a clear message).
  assignProjectAccess: (data) => request('/admin/project-access', {
    method: 'POST',
    body: {
      email: data.email,
      role: data.role,
      project_id: data.project_id,
      team_name: data.team_name,
    },
  }),
  // Assign Roles modal, Section B ("Add new access"). One of:
  //   { email, mode: 'team_member', team_assignments: [{ team_id, role }] }
  //   { email, mode: 'project_admin', project_ids: [...], all_projects: bool }
  //   { email, mode: 'org_admin' }
  // See app/routers/admin.py::assign_roles.
  assignRoles: (data) => request('/admin/assign-roles', { method: 'POST', body: data }),

  // Assign Roles modal, Section A ("Current access").
  updateTeamRole: (membershipId, role) =>
    request(`/admin/team-membership/${encodeURIComponent(membershipId)}`, {
      method: 'PATCH', body: { role },
    }),
  removeTeamMembership: (membershipId) =>
    request(`/admin/team-membership/${encodeURIComponent(membershipId)}`, { method: 'DELETE' }),
  removeProjectAdmin: (scopeId) =>
    request(`/admin/project-admin/${encodeURIComponent(scopeId)}`, { method: 'DELETE' }),
  revokeOrgAdmin: (userId) =>
    request('/admin/revoke-org-admin', { method: 'POST', body: { user_id: userId } }),

  auditLog: (limit = 100) => request(`/admin/audit-log?limit=${encodeURIComponent(limit)}`),

  // no backend yet:
  ...stubMethods('Admin', ['removeUser', 'assignProjectAccessRemove', 'setRole']),
};

// ---------- Confidential-access requests (real backend) ----------
export const accessRequestsApi = {
  // the caller's own requests, newest first — { status, active, team_name, ... }
  mine: () => request('/access-requests/mine'),
  // create a pending request for one team
  create: (teamId) => request('/access-requests', { method: 'POST', body: { team_id: teamId } }),
  // requests the caller may decide (team_lead on that team / project_admin / org_admin)
  pending: () => request('/access-requests/pending'),
  approve: (id) => request(`/access-requests/${encodeURIComponent(id)}/approve`, { method: 'POST' }),
  deny: (id) => request(`/access-requests/${encodeURIComponent(id)}/deny`, { method: 'POST' }),
};

export const ragApi = stub('RAG / retrieval');
export const chatApi = stub('chat');
export const agentsApi = stub('AI agents');
export const studioApi = stub('Studio');
export const notesApi = stub('Notes');
export const notificationsApi = stub('Notifications');

export { ApiError };
