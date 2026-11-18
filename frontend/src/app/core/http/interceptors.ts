import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, throwError } from 'rxjs';
import { ApiError } from '../api/api-error';
import { ErrorReporter } from '../observability/error-reporter';
import { newId, sessionId } from '../observability/ids';

/** Every request gets an `X-Request-ID` (echoed by the backend into its logs) and the tab's session id. */
export const correlationInterceptor: HttpInterceptorFn = (req, next) =>
  next(
    req.clone({
      setHeaders: {
        'X-Request-ID': req.headers.get('X-Request-ID') ?? newId(),
        'X-Client-Session': sessionId(),
      },
    }),
  );

/**
 * Normalises every failure into `ApiError` and reports the ones that indicate
 * a defect or an outage (network errors and 5xx). 4xx are expected outcomes
 * (validation, conflicts) and are shown to the user instead.
 */
export const errorInterceptor: HttpInterceptorFn = (req, next) => {
  const reporter = inject(ErrorReporter);
  return next(req).pipe(
    catchError((raw: unknown) => {
      const error = ApiError.from(raw);
      if (raw instanceof HttpErrorResponse && (error.status === 0 || error.status >= 500)) {
        reporter.report({
          kind: 'http',
          message: `${req.method} ${req.urlWithParams} -> ${error.status} ${error.title}`,
          request_id: error.requestId ?? req.headers.get('X-Request-ID'),
          context: { status: error.status, detail: error.detail },
        });
      }
      return throwError(() => error);
    }),
  );
};
