import { HealthStatus, TelemetryStatus, UserProfile } from '../types';

const API_BASE = '/api';

export async function fetchHealth(): Promise<HealthStatus> {
  const res = await fetch(`${API_BASE}/health`);
  if (!res.ok) {
    throw new Error(`Failed to fetch health: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchTelemetryStatus(): Promise<TelemetryStatus> {
  const res = await fetch(`${API_BASE}/telemetry/status`);
  if (!res.ok) {
    throw new Error(`Failed to fetch telemetry status: ${res.statusText}`);
  }
  return res.json();
}

export async function fetchCurrentUser(): Promise<UserProfile | null> {
  const token = localStorage.getItem('onedrive_rag_token');
  const headers: Record<string, string> = {};
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}/auth/me`, { headers });
  if (!res.ok) {
    return null;
  }
  return res.json();
}

export async function fetchLoginUrl(): Promise<string> {
  const res = await fetch(`${API_BASE}/auth/login`);
  if (!res.ok) {
    throw new Error('Failed to retrieve login URL');
  }
  const data = await res.json();
  return data.auth_url;
}

export async function exchangeAuthCode(code: string, state?: string): Promise<{ token: string; user: UserProfile }> {
  const params = new URLSearchParams({ code });
  if (state) params.set('state', state);
  const res = await fetch(`${API_BASE}/auth/callback?${params.toString()}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to exchange authorization code' }));
    throw new Error(err.detail || 'OAuth exchange failed');
  }
  const data = await res.json();
  if (data.token) {
    localStorage.setItem('onedrive_rag_token', data.token);
  }
  return data;
}

export async function loginSandboxDemo(): Promise<{ token: string; user: UserProfile }> {
  return exchangeAuthCode('mock_dev_code_123');
}

export async function logoutUser(): Promise<void> {
  localStorage.removeItem('onedrive_rag_token');
  localStorage.removeItem('onedrive_rag_demo_session');
  await fetch(`${API_BASE}/auth/logout`, { method: 'POST' }).catch(() => {});
}

