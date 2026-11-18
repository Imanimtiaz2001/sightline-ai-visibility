import { Injectable, NgZone, inject } from '@angular/core';
import {
  Observable,
  catchError,
  defer,
  distinctUntilChanged,
  finalize,
  map,
  shareReplay,
  switchMap,
  takeWhile,
  timer,
} from 'rxjs';
import { RunsApi } from '../api/api.services';
import { Run } from '../api/models';
import { APP_CONFIG } from '../config/app-config';
import { isTerminal } from '../util/labels';

/**
 * Live view of one run.
 *
 * Prefers the server-sent-event stream (`/runs/{id}/events`, pushes on every
 * change). If the stream cannot be opened or drops – proxies, corporate
 * networks – it degrades to polling `GET /runs/{id}`. Either way callers get
 * one Observable that emits each new state and completes after the terminal
 * one. Streams are shared per run id so several components cost one connection.
 */
@Injectable({ providedIn: 'root' })
export class RunTracker {
  private readonly api = inject(RunsApi);
  private readonly zone = inject(NgZone);
  private readonly pollMs = inject(APP_CONFIG).runPollIntervalMs;
  private readonly streams = new Map<string, Observable<Run>>();

  track(runId: string): Observable<Run> {
    let stream = this.streams.get(runId);
    if (!stream) {
      stream = this.sse(runId).pipe(
        catchError(() => this.poll(runId)),
        distinctUntilChanged((a, b) => JSON.stringify(a.stages) === JSON.stringify(b.stages) && a.status === b.status),
        takeWhile((run) => !isTerminal(run.status), true),
        finalize(() => this.streams.delete(runId)),
        shareReplay({ bufferSize: 1, refCount: true }),
      );
      this.streams.set(runId, stream);
    }
    return stream;
  }

  /** Visible for tests. */
  poll(runId: string): Observable<Run> {
    return timer(0, this.pollMs).pipe(
      switchMap(() => this.api.get(runId)),
      takeWhile((run) => !isTerminal(run.status), true),
    );
  }

  private sse(runId: string): Observable<Run> {
    if (typeof EventSource === 'undefined') return this.poll(runId);
    return defer(
      () =>
        new Observable<string>((subscriber) => {
          const source = new EventSource(this.api.eventsUrl(runId));
          let received = false;
          source.addEventListener('run', (event) => {
            received = true;
            this.zone.run(() => subscriber.next((event as MessageEvent<string>).data));
          });
          source.addEventListener('end', () => {
            source.close();
            this.zone.run(() => subscriber.complete());
          });
          source.onerror = () => {
            // EventSource retries forever on its own; we hand over to polling instead,
            // which is bounded and goes through the HTTP interceptors.
            source.close();
            this.zone.run(() => subscriber.error(new Error(received ? 'stream dropped' : 'stream unavailable')));
          };
          return () => source.close();
        }),
    ).pipe(map((data) => JSON.parse(data) as Run));
  }
}
