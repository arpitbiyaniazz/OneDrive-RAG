import { HealthStatus, TelemetryStatus, UserProfile, OneDriveItem } from '../types';

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

export async function fetchGoogleLoginUrl(): Promise<string> {
  const res = await fetch(`${API_BASE}/auth/google/login`);
  if (!res.ok) {
    throw new Error('Failed to retrieve Google login URL');
  }
  const data = await res.json();
  return data.auth_url;
}

export async function exchangeGoogleAuthCode(code: string, state?: string): Promise<{ token: string; user: UserProfile }> {
  const params = new URLSearchParams({ code });
  if (state) params.set('state', state);
  const res = await fetch(`${API_BASE}/auth/google/callback?${params.toString()}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to exchange Google authorization code' }));
    throw new Error(err.detail || 'Google OAuth exchange failed');
  }
  const data = await res.json();
  if (data.token) {
    localStorage.setItem('onedrive_rag_token', data.token);
  }
  return data;
}

export function getAuthHeaders(customHeaders: Record<string, string> = {}): Record<string, string> {
  const token = localStorage.getItem('onedrive_rag_token');
  const headers: Record<string, string> = { ...customHeaders };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

export async function fetchOneDriveTree(): Promise<{ items: OneDriveItem[] }> {
  const res = await fetch(`${API_BASE}/onedrive/tree`, { headers: getAuthHeaders() });
  if (!res.ok) {
    throw new Error('Failed to load OneDrive hierarchy');
  }
  return res.json();
}

export async function fetchGoogleDriveTree(): Promise<{ items: OneDriveItem[] }> {
  const res = await fetch(`${API_BASE}/gdrive/tree`, { headers: getAuthHeaders() });
  if (!res.ok) {
    throw new Error('Failed to load Google Drive hierarchy');
  }
  return res.json();
}

export async function fetchGoogleDriveFolderItems(folderId: string): Promise<{ items: OneDriveItem[] }> {
  const res = await fetch(`${API_BASE}/gdrive/folders/${folderId}/items`, { headers: getAuthHeaders() });
  if (!res.ok) {
    throw new Error('Failed to load Google Drive folder items');
  }
  return res.json();
}

export async function fetchOneDriveFolderItems(folderId: string): Promise<{ items: OneDriveItem[] }> {
  const res = await fetch(`${API_BASE}/onedrive/folders/${folderId}/items`, { headers: getAuthHeaders() });
  if (!res.ok) {
    throw new Error('Failed to load OneDrive folder items');
  }
  return res.json();
}

export async function startIngestion(
  itemIds: string[],
  items?: OneDriveItem[],
  folderPath: string = '/Company'
): Promise<{ job_id: string; total_files: number; message: string }> {
  const payload = {
    item_ids: itemIds,
    items: items?.map((it) => ({
      id: it.id,
      name: it.name,
      drive_type: it.drive_type || 'onedrive',
      mime_type: it.mime_type,
    })),
    folder_path: folderPath,
  };
  const res = await fetch(`${API_BASE}/ingestion/start`, {
    method: 'POST',
    headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Failed to start ingestion' }));
    throw new Error(err.detail || 'Ingestion failed');
  }
  return res.json();
}

export async function fetchDocuments(filters?: {
  folder_path?: string;
  file_type?: string;
  drive_type?: string;
}) {
  const params = new URLSearchParams();
  if (filters?.folder_path) params.set('folder_path', filters.folder_path);
  if (filters?.file_type && filters.file_type !== 'all') params.set('file_type', filters.file_type);
  if (filters?.drive_type && filters.drive_type !== 'all') params.set('drive_type', filters.drive_type);

  const query = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${API_BASE}/documents${query}`, { headers: getAuthHeaders() });
  if (!res.ok) {
    throw new Error('Failed to fetch documents');
  }
  return res.json();
}

export async function fetchKnowledgeBaseStats() {
  const res = await fetch(`${API_BASE}/documents/stats`, { headers: getAuthHeaders() });
  if (!res.ok) {
    throw new Error('Failed to fetch stats');
  }
  return res.json();
}

export async function logoutUser(): Promise<void> {
  localStorage.removeItem('onedrive_rag_token');
  localStorage.removeItem('onedrive_rag_demo_session');
  await fetch(`${API_BASE}/auth/logout`, { method: 'POST', headers: getAuthHeaders() }).catch(() => {});
}


