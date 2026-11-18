import { HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { ApiError } from './api-error';

describe('ApiError', () => {
  it('maps problem+json, field errors and the request id', () => {
    const error = ApiError.from(
      new HttpErrorResponse({
        status: 422,
        headers: new HttpHeaders({ 'X-Request-ID': 'header-id' }),
        error: {
          title: 'Validation failed',
          status: 422,
          detail: 'the request body is invalid',
          request_id: 'abc123',
          errors: [
            { location: 'body', field: 'competitors.0.domain', message: 'must be a domain', type: 'value_error' },
            { location: 'body', field: null, message: 'competitor domains must be unique', type: 'value_error' },
          ],
        },
      }),
    );
    expect(error.status).toBe(422);
    expect(error.requestId).toBe('abc123');
    expect(error.fieldErrors['competitors.0.domain']).toBe('must be a domain');
    expect(error.fieldErrors['_']).toContain('unique');
    expect(error.isRetryable).toBe(false);
    expect(error.userMessage).toBe('the request body is invalid');
  });

  it('turns network failures into a retryable, human message', () => {
    const error = ApiError.from(new HttpErrorResponse({ status: 0, statusText: 'Unknown Error' }));
    expect(error.isNetwork).toBe(true);
    expect(error.isRetryable).toBe(true);
    expect(error.userMessage).toContain('could not be reached');
  });

  it('never leaks raw server errors for 5xx', () => {
    const error = ApiError.from(new HttpErrorResponse({ status: 500, error: '<html>Traceback…</html>' }));
    expect(error.userMessage).not.toContain('Traceback');
    expect(error.isRetryable).toBe(true);
  });
});
