export interface HealthStatus {
  status: string;
  version: string;
  sandbox_mode: boolean;
}

export interface UserProfile {
  id: string;
  email: string;
  full_name?: string;
  onedrive_connected?: boolean;
  gdrive_connected?: boolean;
  sandbox_mode?: boolean;
}

export type DriveType = 'onedrive' | 'google_drive';

export interface OneDriveItem {
  id: string;
  name: string;
  is_folder: boolean;
  size?: number;
  mime_type?: string;
  modified_date?: string;
  web_url?: string;
  path: string;
  drive_type?: DriveType;
  supported?: boolean;
}

export type DocumentStatus = 'PENDING' | 'INDEXED' | 'FAILED' | 'UNSUPPORTED' | 'DELETED';

export interface KnowledgeBaseStats {
  /** Number of INDEXED documents. */
  total_documents: number;
  by_status: Partial<Record<DocumentStatus, number>>;
  total_chunks: number;
  last_sync: string | null;
}

/** A folder selected for indexing: new files that appear in it are indexed on every sync. */
export interface TrackedFolder {
  id: string;
  drive_type: DriveType;
  name: string;
  path: string;
  created_at: string | null;
  last_synced_at: string | null;
}

export interface DocumentRecord {
  id: string;
  filename: string;
  file_type: string;
  drive_type?: DriveType;
  folder_path: string;
  file_size: number | null;
  modified_date: string | null;
  url?: string | null;
  status: DocumentStatus;
  chunk_count: number;
  error_message?: string | null;
  last_ingested_at?: string | null;
}

export interface Citation {
  source_id: number;
  document_id?: string;
  filename: string;
  file_type?: string;
  location?: string | null;
  page?: number | null;
  section?: string | null;
  drive_type?: DriveType;
  url?: string | null;
}

/** A document the user selected for a question (by typing "/" in the chat input); it is answered from these only. */
export interface SelectedDocument {
  id: string;
  filename: string;
  file_type: string;
  drive_type?: DriveType;
}

export interface ChatMessage {
  id: string;
  /** Stable render key for messages created in this tab (their id changes to the server id). */
  clientId?: string;
  role: 'user' | 'assistant';
  content: string;
  citations?: Citation[];
  /** User messages: the documents the question was limited to. */
  documents?: SelectedDocument[];
  feedback?: number | null;
  timestamp: string;
  persisted?: boolean;
  isError?: boolean;
}

export interface IngestionFileResult {
  name: string;
  status: string;
  reason?: string;
}

export type JobStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'COMPLETED_WITH_ERRORS' | 'FAILED';

export interface IngestionJob {
  job_id: string;
  job_type: 'ingest' | 'sync';
  status: JobStatus;
  total_files: number;
  processed_files: number;
  failed_files: number;
  total_chunks: number;
  progress_percent: number;
  result?: { files?: IngestionFileResult[]; summary?: Record<string, number> } | null;
  error_message?: string | null;
  created_at?: string | null;
  finished_at?: string | null;
}
