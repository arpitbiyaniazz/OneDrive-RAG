import { HealthStatus, TelemetryStatus } from '../types';

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
