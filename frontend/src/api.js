const API = 'http://localhost:8000';

// Get stored auth token
function getToken() {
  return localStorage.getItem('irp_token');
}

function getTenantId() {
  return localStorage.getItem('irp_tenant_id') || 'default';
}

// Auth headers helper
function authHeaders() {
  const token = getToken();
  const headers = { 'Content-Type': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

// ── Auth ──────────────────────────────────────────────────────
export async function login(tenant_id, password) {
  const res = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tenant_id, password }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || 'Login failed');
  }
  const data = await res.json();
  localStorage.setItem('irp_token', data.token);
  localStorage.setItem('irp_tenant_id', data.tenant_id);
  localStorage.setItem('irp_org_name', data.org_name);
  return data;
}

export async function register(tenant_id, org_name, password) {
  const res = await fetch(`${API}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tenant_id, org_name, password }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || 'Registration failed');
  }
  const data = await res.json();
  localStorage.setItem('irp_token', data.token);
  localStorage.setItem('irp_tenant_id', data.tenant_id);
  localStorage.setItem('irp_org_name', data.org_name);
  return data;
}

export function logout() {
  localStorage.removeItem('irp_token');
  localStorage.removeItem('irp_tenant_id');
  localStorage.removeItem('irp_org_name');
}

export function isLoggedIn() {
  return !!getToken();
}

export function getCurrentUser() {
  return {
    tenant_id: localStorage.getItem('irp_tenant_id'),
    org_name: localStorage.getItem('irp_org_name'),
    token: getToken(),
  };
}

// ── Dashboard ─────────────────────────────────────────────────
export async function fetchIncidents(status = null) {
  const params = new URLSearchParams({ limit: '50' });
  if (status) params.set('status', status);
  const res = await fetch(`${API}/dashboard/incidents?${params}`, { headers: authHeaders() });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function fetchIncidentDetail(id) {
  const res = await fetch(`${API}/dashboard/incidents/${id}`, { headers: authHeaders() });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function fetchStats() {
  const res = await fetch(`${API}/dashboard/stats`, { headers: authHeaders() });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function fireAlert(data) {
  const res = await fetch(`${API}/alerts/manual`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function fetchPendingApprovals() {
  const res = await fetch(`${API}/approvals/pending`, { headers: authHeaders() });
  return res.json();
}

export async function approveAction(requestId) {
  const res = await fetch(`${API}/approvals/${requestId}/approve?approved_by=dashboard`, {
    method: 'POST',
    headers: authHeaders(),
  });
  return res.json();
}

export async function rejectAction(requestId) {
  const res = await fetch(`${API}/approvals/${requestId}/reject?approved_by=dashboard`, {
    method: 'POST',
    headers: authHeaders(),
  });
  return res.json();
}

export async function fetchCredentials() {
  const res = await fetch(`${API}/credentials/`, { headers: authHeaders() });
  return res.json();
}

export async function saveCredential(type, data) {
  const res = await fetch(`${API}/credentials/${type}`, {
    method: 'PUT',
    headers: authHeaders(),
    body: JSON.stringify(data),
  });
  return res.json();
}
