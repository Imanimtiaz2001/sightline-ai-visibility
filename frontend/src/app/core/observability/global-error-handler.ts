import { ErrorHandler, Injectable, inject } from '@angular/core';
import { ApiError } from '../api/api-error';
import { ErrorReporter } from './error-reporter';

/** Catches everything Angular does not: template errors, unhandled rejections, uncaught exceptions. */
@Injectable()
export class GlobalErrorHandler implements ErrorHandler {
  private readonly reporter = inject(ErrorReporter);

  handleError(error: unknown): void {
    const unwrapped = (error as { rejection?: unknown })?.rejection ?? error;
    if (unwrapped instanceof ApiError) return; // already reported by the HTTP interceptor
    const err = unwrapped instanceof Error ? unwrapped : new Error(String(unwrapped));
    console.error(err);
    this.reporter.report({
      kind: (error as { rejection?: unknown })?.rejection !== undefined ? 'unhandled_rejection' : 'error',
      message: `${err.name}: ${err.message}`,
      stack: err.stack,
    });
  }
}
