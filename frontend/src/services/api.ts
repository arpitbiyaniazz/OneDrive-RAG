import { DocumentRecord, DriveType, HealthStatus, IngestionJob, OneDriveItem, UserProfile } from '../types';

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) || '/api';
const TOKEN_KEY = 'onedrive_rag_token';
const OAUTH_STATE_KEY = 'onedrive_rag_oauth_state';

export type OAuthProvider = 'microsoft' | 'google';

/** Fired when the server rejects the session so the app can return to the login page. */
export const AUTH_EXPIRED_EVENT = 'auth-expired';

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

function getAuthHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = getToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : { ...extra };
}

async function errorFrom(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => ({}));
  const detail = body?.detail;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    return new ApiError(detail.message || 'Request failed', res.status, detail.code);
  }
  if (Array.isArray(detail)) {
    return new ApiError(detail.map((d: { msg?: string }) => d.msg).join('; ') || 'Invalid request', res.status);
  }
  return new ApiError(typeof detail === 'string' ? detail : `Request failed (${res.status})`, res.status);
}

/** fetch() against the API with the session token; a 401 signs the user out. */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: getAuthHeaders((init.headers as Record<string, string>) || {}),
  });
  if (res.status === 401 && getToken() && !path.startsWith('/auth/')) {
    localStorage.removeItem(TOKEN_KEY);
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  }
  return res;
}

async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await apiFetch(path, init);
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

const jsonBody = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const fetchHealth = () => apiJson<HealthStatus>('/health');

export async function fetchCurrentUser(): Promise<UserProfile | null> {
  if (!getToken()) return null;
  const res = await apiFetch('/auth/me');
  if (!res.ok) {
    if (res.status === 401) localStorage.removeItem(TOKEN_KEY);
    return null;
  }
  return res.json();
}

/**
 * Starts an OAuth redirect. The server-issued state is remembered for this tab so the callback
 * can reject responses to sign-ins this browser never started (login CSRF).
 * When already signed in, the provider account is linked to the current user.
 */
export async function startOAuth(provider: OAuthProvider): Promise<void> {
  const path = provider === 'google' ? '/auth/google/login' : '/auth/login';
  const data = await apiJson<{ auth_url: string; state: string }>(path);
  sessionStorage.setItem(OAUTH_STATE_KEY, JSON.stringify({ provider, state: data.state }));
  window.location.href = data.auth_url;
}

export async function completeOAuth(provider: OAuthProvider, code: string, state: string | null): Promise<UserProfile> {
  const saved = JSON.parse(sessionStorage.getItem(OAUTH_STATE_KEY) || 'null');
  sessionStorage.removeItem(OAUTH_STATE_KEY);
  if (!saved || saved.provider !== provider || !state || saved.state !== state) {
    throw new ApiError('This sign-in link was not started from this browser. Please sign in again.', 400);
  }
  const path = provider === 'google' ? '/auth/google/callback' : '/auth/callback';
  const params = new URLSearchParams({ code, state });
  const data = await apiJson<{ token: string; user: UserProfile }>(`${path}?${params.toString()}`);
  localStorage.setItem(TOKEN_KEY, data.token);
  return data.user;
}

export async function loginSandbox(): Promise<UserProfile> {
  const data = await apiJson<{ token: string; user: UserProfile }>('/auth/sandbox', { method: 'POST' });
  localStorage.setItem(TOKEN_KEY, data.token);
  return data.user;
}

export async function logoutUser(): Promise<void> {
  await apiFetch('/auth/logout', { method: 'POST' }).catch(() => undefined);
  localStorage.removeItem(TOKEN_KEY);
}

const driveAlias = (drive: DriveType) => (drive === 'google_drive' ? 'gdrive' : 'onedrive');

export const fetchDriveRoot = (drive: DriveType) =>
  apiJson<{ items: OneDriveItem[]; root_id: string; source: string }>(`/${driveAlias(drive)}/tree`);

export const fetchFolderItems = (drive: DriveType, folderId: string) =>
  apiJson<{ items: OneDriveItem[] }>(`/${driveAlias(drive)}/folders/${encodeURIComponent(folderId)}/items`);

export interface IngestSelection {
  id: string;
  drive_type: DriveType;
  is_folder: boolean;
  name?: string;
}

export const startIngestion = (items: IngestSelection[]) => apiJson<IngestionJob>('/ingestion/start', jsonBody({ items }));

export const fetchJob = (jobId: string) => apiJson<IngestionJob>(`/ingestion/${encodeURIComponent(jobId)}`);

export const startSync = () => apiJson<IngestionJob>('/sync/start', { method: 'POST' });

export async function fetchDocuments(filters: { file_type?: string; drive_type?: string } = {}) {
  const params = new URLSearchParams();
  if (filters.file_type && filters.file_type !== 'all') params.set('file_type', filters.file_type);
  if (filters.drive_type && filters.drive_type !== 'all') params.set('drive_type', filters.drive_type);
  const query = params.toString() ? `?${params.toString()}` : '';
  return apiJson<{ total: number; documents: DocumentRecord[] }>(`/documents${query}`);
}

export const fetchKnowledgeBaseStats = () =>
  apiJson<{ total_documents: number; total_chunks: number; last_sync: string | null; by_status: Record<string, number> }>(
    '/documents/stats'
  );

export async function deleteDocument(documentId: string): Promise<void> {
  const res = await apiFetch(`/documents/${encodeURIComponent(documentId)}`, { method: 'DELETE' });
  if (!res.ok) throw await errorFrom(res);
}

export interface ChatSessionResponseItem {
  id: string;
  title: string;
  created_at?: string;
  updated_at?: string;
}

export async function fetchChatSessions(): Promise<ChatSessionResponseItem[]> {
  return (await apiJson<{ sessions: ChatSessionResponseItem[] }>('/chat/sessions')).sessions;
}

export async function deleteChatSession(sessionId: string): Promise<void> {
  const res = await apiFetch(`/chat/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
  if (!res.ok) throw await errorFrom(res);
}

export const fetchSessionMessages = (sessionId: string) =>
  apiJson<{ messages: Array<{ id: string; role: 'user' | 'assistant'; content: string; citations: []; feedback: number | null; created_at: string }> }>(
    `/chat/sessions/${encodeURIComponent(sessionId)}/messages`
  );

export const sendFeedback = (messageId: string, feedback: 1 | -1) =>
  apiJson<{ status: string }>('/chat/feedback', jsonBody({ message_id: messageId, feedback }));

export { errorFrom };
