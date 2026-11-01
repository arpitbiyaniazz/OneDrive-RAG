import {
  Citation,
  DocumentRecord,
  DriveType,
  HealthStatus,
  IngestionJob,
  JobStatus,
  KnowledgeBaseStats,
  OneDriveItem,
  SelectedDocument,
  TrackedFolder,
  UserProfile,
} from '../types';

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) || '/api';
const TOKEN_KEY = 'onedrive_rag_token';
const OAUTH_STATE_KEY = 'onedrive_rag_oauth_state';

/** localStorage key of the session token; the app watches it to follow sign-in/out in other tabs. */
export const TOKEN_STORAGE_KEY = TOKEN_KEY;

// Microsoft sign-in is turned off: only Google sign-in is used.
// export type OAuthProvider = 'microsoft' | 'google';
export type OAuthProvider = 'google';

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

export const hasSessionToken = (): boolean => Boolean(getToken());

function getAuthHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = getToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : { ...extra };
}

function fallbackMessage(status: number): string {
  if (status === 429) return 'Too many requests. Please wait a moment and try again.';
  return `Request failed (${status})`;
}

/** Builds an ApiError from an error response; a backend `detail` message is surfaced as-is. */
async function errorFrom(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => ({}));
  const detail = body?.detail;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    return new ApiError(detail.message || fallbackMessage(res.status), res.status, detail.code);
  }
  if (Array.isArray(detail)) {
    return new ApiError(detail.map((d: { msg?: string }) => d.msg).join('; ') || 'Invalid request', res.status);
  }
  return new ApiError(typeof detail === 'string' && detail ? detail : fallbackMessage(res.status), res.status);
}

/**
 * True when a response says the app session itself is invalid. The auth dependency marks those 401s
 * with `WWW-Authenticate: Bearer` (a cross-origin deployment may not expose that header, so any 401
 * that is not a drive problem counts too). A missing drive connection is a 403 and never signs out.
 */
async function isSessionRejected(res: Response): Promise<boolean> {
  if (res.status !== 401) return false;
  if (res.headers.has('WWW-Authenticate')) return true;
  const body = await res.clone().json().catch(() => null);
  return body?.detail?.code !== 'drive_not_connected';
}

/** fetch() against the API with the session token; a rejected session signs the user out. */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: getAuthHeaders((init.headers as Record<string, string>) || {}),
  });
  // /auth/* callers handle their own 401s. Fire even when the token is already gone (signed out in another tab).
  if (!path.startsWith('/auth/') && (await isSessionRejected(res))) {
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

async function apiDelete(path: string): Promise<void> {
  const res = await apiFetch(path, { method: 'DELETE' });
  if (!res.ok) throw await errorFrom(res);
}

const jsonBody = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const isJobRunning = (status?: JobStatus | null) => status === 'PENDING' || status === 'PROCESSING';

export const fetchHealth = () => apiJson<HealthStatus>('/health');

/**
 * The signed-in user, or null when there is no valid session (only a 401 ends it). Throws when the server
 * cannot answer (restarting, offline): the session is kept, so a reload never signs the user out.
 */
export async function fetchCurrentUser(): Promise<UserProfile | null> {
  if (!getToken()) return null;
  const res = await apiFetch('/auth/me');
  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    return null;
  }
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

const RENEW_MIN_INTERVAL_MS = 60 * 60 * 1000;
let lastRenewal = 0;

/**
 * Swaps the session token for a fresh one (at most once an hour), so a user who keeps using the app stays
 * signed in; the server still ends every session some weeks after the sign-in.
 */
export async function renewSession(): Promise<void> {
  const sent = getToken();
  if (!sent || Date.now() - lastRenewal < RENEW_MIN_INTERVAL_MS) return;
  lastRenewal = Date.now();
  const res = await apiFetch('/auth/refresh', { method: 'POST' });
  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
    return;
  }
  if (!res.ok) {
    lastRenewal = 0; // a server hiccup: try again next time
    return;
  }
  const { token } = (await res.json()) as { token?: string };
  // Not after a sign-out or another account's sign-in in the meantime.
  if (token && getToken() === sent) localStorage.setItem(TOKEN_KEY, token);
}

interface PendingOAuth {
  provider: OAuthProvider;
  state: string;
  code_verifier: string;
}

function takePendingOAuth(): PendingOAuth | null {
  try {
    const saved = JSON.parse(sessionStorage.getItem(OAUTH_STATE_KEY) || 'null');
    return saved && typeof saved === 'object' ? saved : null;
  } catch {
    return null;
  } finally {
    sessionStorage.removeItem(OAUTH_STATE_KEY);
  }
}

/** Forgets an unfinished sign-in (used when the provider redirects back with an error). */
export function discardOAuthAttempt(): void {
  sessionStorage.removeItem(OAUTH_STATE_KEY);
}

/**
 * Starts an OAuth redirect. The server-issued state and PKCE verifier are remembered for this tab
 * only: the callback rejects responses to sign-ins this browser never started (login CSRF), and an
 * intercepted redirect URL (code + state) is not enough to sign in without the verifier.
 * When already signed in, the provider account is linked to the current user.
 */
export async function startOAuth(provider: OAuthProvider): Promise<void> {
  // const path = provider === 'google' ? '/auth/google/login' : '/auth/login';  // Microsoft sign-in is turned off
  const path = '/auth/google/login';
  const data = await apiJson<{ auth_url: string; state: string; code_verifier: string }>(path);
  const pending: PendingOAuth = { provider, state: data.state, code_verifier: data.code_verifier };
  sessionStorage.setItem(OAUTH_STATE_KEY, JSON.stringify(pending));
  window.location.href = data.auth_url;
}

export async function completeOAuth(provider: OAuthProvider, code: string, state: string | null): Promise<UserProfile> {
  const saved = takePendingOAuth();
  if (!saved || saved.provider !== provider || !state || saved.state !== state || !saved.code_verifier) {
    throw new ApiError('This sign-in link was not started from this browser. Please sign in again.', 400);
  }
  // const path = provider === 'google' ? '/auth/google/callback' : '/auth/callback';  // Microsoft sign-in is turned off
  const path = '/auth/google/callback';
  const data = await apiJson<{ token: string; user: UserProfile }>(
    path,
    jsonBody({ code, state, code_verifier: saved.code_verifier })
  );
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

/** Queues indexing; selected folders also become tracked folders. 429 when 3 jobs are already queued. */
export const startIngestion = (items: IngestSelection[]) => apiJson<IngestionJob>('/ingestion/start', jsonBody({ items }));

export const fetchJob = (jobId: string) => apiJson<IngestionJob>(`/ingestion/${encodeURIComponent(jobId)}`);

export const startSync = () => apiJson<IngestionJob>('/sync/start', { method: 'POST' });

export const fetchSyncStatus = () => apiJson<{ last_sync: IngestionJob | null }>('/sync/status');

export async function fetchDocuments(filters: { file_type?: string; drive_type?: string } = {}) {
  const params = new URLSearchParams();
  if (filters.file_type && filters.file_type !== 'all') params.set('file_type', filters.file_type);
  if (filters.drive_type && filters.drive_type !== 'all') params.set('drive_type', filters.drive_type);
  const query = params.toString() ? `?${params.toString()}` : '';
  return apiJson<{ total: number; documents: DocumentRecord[] }>(`/documents${query}`);
}

/** Indexed documents, the ones a question can be limited to (most recently indexed first). */
export async function fetchIndexedDocuments(): Promise<DocumentRecord[]> {
  return (await apiJson<{ total: number; documents: DocumentRecord[] }>('/documents?status=indexed&limit=1000')).documents;
}

export const fetchKnowledgeBaseStats = () => apiJson<KnowledgeBaseStats>('/documents/stats');

/** Removes a document from the knowledge base; it stays out of syncs until selected for indexing again. */
export const deleteDocument = (documentId: string) => apiDelete(`/documents/${encodeURIComponent(documentId)}`);

export async function fetchTrackedFolders(): Promise<TrackedFolder[]> {
  return (await apiJson<{ folders: TrackedFolder[] }>('/tracked-folders')).folders;
}

/** Stops indexing new files from a folder; documents already indexed from it stay. */
export const untrackFolder = (folderId: string) => apiDelete(`/tracked-folders/${encodeURIComponent(folderId)}`);

export interface ChatSessionResponseItem {
  id: string;
  title: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export async function fetchChatSessions(): Promise<ChatSessionResponseItem[]> {
  return (await apiJson<{ sessions: ChatSessionResponseItem[] }>('/chat/sessions')).sessions;
}

export const deleteChatSession = (sessionId: string) => apiDelete(`/chat/sessions/${encodeURIComponent(sessionId)}`);

export interface SessionMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  citations: Citation[];
  documents?: SelectedDocument[];
  feedback: number | null;
  created_at: string | null;
}

export const fetchSessionMessages = (sessionId: string) =>
  apiJson<{ session_id: string; title: string; messages: SessionMessage[] }>(
    `/chat/sessions/${encodeURIComponent(sessionId)}/messages`
  );

export const sendFeedback = (messageId: string, feedback: 1 | -1) =>
  apiJson<{ status: string }>('/chat/feedback', jsonBody({ message_id: messageId, feedback }));

export { errorFrom };
