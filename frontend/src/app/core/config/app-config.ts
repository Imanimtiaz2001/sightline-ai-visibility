import { InjectionToken } from '@angular/core';

/**
 * Runtime configuration, loaded from `/config.json` before bootstrap so the same
 * build artefact runs in every environment (the container writes the file from
 * environment variables at start-up; see frontend/.env.example).
 */
export interface AppConfig {
  /** Base URL of the backend API, e.g. "/api/v1" (proxied) or "https://api.example.com/api/v1". */
  apiBaseUrl: string;
  /** Send browser errors to the backend (`POST /client-errors`). */
  errorReporting: boolean;
  /** Polling interval used when the live progress stream is unavailable. */
  runPollIntervalMs: number;
  /** Release identifier attached to error reports. */
  release: string;
}

export const DEFAULT_CONFIG: AppConfig = {
  apiBaseUrl: '/api/v1',
  errorReporting: true,
  runPollIntervalMs: 2000,
  release: 'dev',
};

export const APP_CONFIG = new InjectionToken<AppConfig>('APP_CONFIG');

export async function loadConfig(): Promise<AppConfig> {
  try {
    const response = await fetch('/config.json', { cache: 'no-store' });
    if (!response.ok) return DEFAULT_CONFIG;
    const raw = (await response.json()) as Partial<AppConfig>;
    return {
      apiBaseUrl: typeof raw.apiBaseUrl === 'string' && raw.apiBaseUrl ? raw.apiBaseUrl.replace(/\/$/, '') : DEFAULT_CONFIG.apiBaseUrl,
      errorReporting: typeof raw.errorReporting === 'boolean' ? raw.errorReporting : DEFAULT_CONFIG.errorReporting,
      runPollIntervalMs: Number(raw.runPollIntervalMs) >= 500 ? Number(raw.runPollIntervalMs) : DEFAULT_CONFIG.runPollIntervalMs,
      release: typeof raw.release === 'string' && raw.release ? raw.release : DEFAULT_CONFIG.release,
    };
  } catch {
    return DEFAULT_CONFIG;
  }
}
