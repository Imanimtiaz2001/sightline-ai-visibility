import { HttpErrorResponse } from '@angular/common/http';
import { ProblemDetails } from './models';

/**
 * One error shape for the whole UI. Components never read raw HttpErrorResponses:
 * they get a human message, field errors for forms and the request id to quote.
 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly title: string,
    readonly detail: string,
    readonly requestId: string | null,
    readonly fieldErrors: Record<string, string> = {},
  ) {
    super(detail || title);
    this.name = 'ApiError';
  }

  get isNetwork(): boolean {
    return this.status === 0;
  }

  get isRetryable(): boolean {
    return this.status === 0 || this.status === 429 || this.status >= 500;
  }

  /** Plain-language message suitable for an error state or toast. */
  get userMessage(): string {
    if (this.isNetwork) return 'The service could not be reached. Check your connection and try again.';
    if (this.status === 429) return 'Too many analyses are running right now. Try again in a minute.';
    if (this.status === 503) return this.detail || 'The service is not configured yet.';
    if (this.status >= 500) return 'Something went wrong on the server. Try again; if it keeps happening, share the reference below.';
    return this.detail || this.title;
  }

  static from(error: unknown): ApiError {
    if (error instanceof ApiError) return error;
    if (error instanceof HttpErrorResponse) {
      const requestId = error.headers?.get('X-Request-ID') ?? null;
      const body = error.error as Partial<ProblemDetails> | null;
      if (body && typeof body === 'object' && typeof body.title === 'string') {
        const fields: Record<string, string> = {};
        for (const e of body.errors ?? []) {
          const key = e.field ?? '_';
          fields[key] = fields[key] ? fields[key] : e.message;
        }
        return new ApiError(error.status, body.title, body.detail ?? '', body.request_id ?? requestId, fields);
      }
      return new ApiError(error.status, error.statusText || 'Request failed', '', requestId);
    }
    return new ApiError(0, 'Unexpected error', error instanceof Error ? error.message : String(error), null);
  }
}
