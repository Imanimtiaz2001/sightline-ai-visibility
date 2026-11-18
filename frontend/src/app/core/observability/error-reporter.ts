import { Injectable, inject } from '@angular/core';
import { Router } from '@angular/router';
import { APP_CONFIG } from '../config/app-config';
import { newId, sessionId } from './ids';

export type ClientErrorKind = 'error' | 'unhandled_rejection' | 'http' | 'resource';

export interface ClientErrorReport {
  kind: ClientErrorKind;
  message: string;
  stack?: string;
  request_id?: string | null;
  trace_id?: string | null;
  context?: Record<string, unknown>;
}

const MAX_PER_MINUTE = 10;
const DEDUPE_WINDOW_MS = 30_000;

/**
 * Sends browser failures to the backend (`POST /client-errors`) with the route,
 * the session id and – when known – the request id / run trace id, so an
 * engineer can join a user's report to the server logs of the same request.
 * Uses `fetch` with keepalive (not HttpClient) so reporting can never recurse
 * through the HTTP interceptors, and survives page unloads.
 */
@Injectable({ providedIn: 'root' })
export class ErrorReporter {
  private readonly config = inject(APP_CONFIG);
  private readonly router = inject(Router);
  private readonly recent = new Map<string, number>();
  private sentThisMinute: number[] = [];

  report(report: ClientErrorReport): void {
    if (!this.config.errorReporting) return;
    const now = Date.now();
    const fingerprint = `${report.kind}:${report.message.slice(0, 200)}`;
    const last = this.recent.get(fingerprint);
    if (last !== undefined && now - last < DEDUPE_WINDOW_MS) return;
    this.sentThisMinute = this.sentThisMinute.filter((t) => now - t < 60_000);
    if (this.sentThisMinute.length >= MAX_PER_MINUTE) return;
    this.recent.set(fingerprint, now);
    this.sentThisMinute.push(now);

    const body = {
      kind: report.kind,
      message: report.message.slice(0, 1000),
      stack: report.stack?.slice(0, 8000),
      url: location.href.slice(0, 1000),
      route: this.router.url.slice(0, 300),
      request_id: report.request_id ?? undefined,
      trace_id: report.trace_id ?? undefined,
      session_id: sessionId(),
      release: this.config.release,
      context: { ...report.context, viewport: `${innerWidth}x${innerHeight}` },
    };
    void fetch(`${this.config.apiBaseUrl}/client-errors`, {
      method: 'POST',
      keepalive: true,
      headers: { 'Content-Type': 'application/json', 'X-Request-ID': newId() },
      body: JSON.stringify(body),
    }).catch(() => undefined); // reporting must never throw
  }
}
