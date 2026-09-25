export interface HealthStatus {
  status: string;
  project: string;
  version: string;
  environment: string;
  mock_onedrive_mode: boolean;
}

export interface TelemetryStatus {
  opentelemetry: {
    service_name: string;
    tracer_active: boolean;
    otlp_endpoint: string;
  };
  langfuse: {
    host: string;
    client_active: boolean;
    configured: boolean;
  };
}

export interface UserProfile {
  id: string;
  email: string;
  full_name?: string;
  is_active?: boolean;
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
  children?: OneDriveItem[];
}

export type CloudItem = OneDriveItem;

export interface DocumentRecord {
  id: string;
  filename: string;
  file_type: string;
  drive_type?: DriveType;
  folder_path: string;
  file_size: number;
  modified_date: string;
  onedrive_url?: string;
  status: 'PENDING' | 'INDEXED' | 'FAILED' | 'DELETED';
  last_ingested_at?: string;
}

export interface Citation {
  source_id: number;
  filename: string;
  page?: number;
  section?: string;
  drive_type?: DriveType;
  onedrive_url?: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  citations?: Citation[];
  langfuse_trace_id?: string;
  feedback?: number;
  timestamp: string;
}

export interface IngestionJob {
  id: string;
  status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';
  folder_path: string;
  total_files: number;
  processed_files: number;
  failed_files: number;
  total_chunks: number;
  error_message?: string;
}
