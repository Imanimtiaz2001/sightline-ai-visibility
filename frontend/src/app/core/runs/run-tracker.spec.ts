import { TestBed } from '@angular/core/testing';
import { Observable, lastValueFrom, of, toArray } from 'rxjs';
import { RunsApi } from '../api/api.services';
import { Run, RunStatus } from '../api/models';
import { APP_CONFIG, DEFAULT_CONFIG } from '../config/app-config';
import { RunTracker } from './run-tracker';

function run(status: RunStatus, stage = 'plan'): Run {
  return { id: 'r1', status, stages: [{ stage, label: stage, status: 'ok', detail: null, completed: 1, total: 1 }] } as unknown as Run;
}

describe('RunTracker', () => {
  it('falls back to polling without EventSource and completes on the terminal state', async () => {
    const states = [run('queued'), run('running', 'discover'), run('running', 'measure'), run('partial', 'report'), run('partial', 'report')];
    let calls = 0;
    const api: Partial<RunsApi> = { get: (): Observable<Run> => of(states[Math.min(calls++, states.length - 1)]) };
    TestBed.configureTestingModule({
      providers: [
        { provide: RunsApi, useValue: api },
        { provide: APP_CONFIG, useValue: { ...DEFAULT_CONFIG, runPollIntervalMs: 5 } },
      ],
    });
    const tracker = TestBed.inject(RunTracker);
    const seen = await lastValueFrom(tracker.track('r1').pipe(toArray()));
    expect(seen.map((r) => r.status)).toEqual(['queued', 'running', 'running', 'partial']);
    expect(calls).toBe(4); // stops polling once the run is terminal
  });

  it('shares one stream per run between subscribers', () => {
    TestBed.configureTestingModule({
      providers: [
        { provide: RunsApi, useValue: { get: () => of(run('succeeded')) } },
        { provide: APP_CONFIG, useValue: DEFAULT_CONFIG },
      ],
    });
    const tracker = TestBed.inject(RunTracker);
    const a = tracker.track('r1');
    expect(tracker.track('r1')).toBe(a);
  });
});
