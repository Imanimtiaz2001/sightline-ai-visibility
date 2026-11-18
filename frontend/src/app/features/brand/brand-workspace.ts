import { DestroyRef, Injectable, computed, inject, signal } from '@angular/core';
import { Observable, Subscription, tap } from 'rxjs';
import { BrandsApi, InsightsApi, RunsApi } from '../../core/api/api.services';
import { Brand, Page, QueryPage, Recommendation, Run, RunBrief } from '../../core/api/models';
import { RunTracker } from '../../core/runs/run-tracker';
import { PortfolioStore } from '../../core/state/stores';
import { isTerminal } from '../../core/util/labels';
import { Loadable } from '../../core/util/loadable';

/**
 * State for one brand's workspace, provided by the brand route and shared by its
 * tabs (overview, opportunities, recommendations, history). It owns:
 *   - the brand and its run history,
 *   - which run is being viewed (the URL's `?run=` or the latest completed one),
 *   - that run's report, measured queries and recommendations,
 *   - the live state of an in-progress analysis, refreshing everything when it ends.
 */
@Injectable()
export class BrandWorkspace {
  private readonly brands = inject(BrandsApi);
  private readonly runs = inject(RunsApi);
  private readonly insights = inject(InsightsApi);
  private readonly tracker = inject(RunTracker);
  private readonly portfolio = inject(PortfolioStore);
  private activeSub: Subscription | null = null;
  private loadedRunId: string | null = null;

  readonly brandId = signal<string>('');
  private readonly requestedRunId = signal<string | null>(null);

  readonly brand = new Loadable<Brand>();
  readonly history = new Loadable<Page<RunBrief>>();
  readonly run = new Loadable<Run>();
  readonly queries = new Loadable<QueryPage>();
  readonly recommendations = new Loadable<Recommendation[]>();
  readonly activeRun = signal<Run | null>(null);
  /** The analysis that just finished while this workspace was open (to announce its outcome). */
  readonly justFinished = signal<Run | null>(null);

  /** The run whose results are on screen. */
  readonly viewedRunId = computed(() => this.requestedRunId() ?? this.brand.value()?.latest_completed_run?.id ?? null);
  readonly isLatest = computed(() => {
    const latest = this.brand.value()?.latest_completed_run?.id;
    return !this.requestedRunId() || this.requestedRunId() === latest;
  });
  readonly hasResults = computed(() => !!this.viewedRunId());

  constructor() {
    inject(DestroyRef).onDestroy(() => this.activeSub?.unsubscribe());
  }

  open(brandId: string, runId: string | null): void {
    const brandChanged = brandId !== this.brandId();
    this.brandId.set(brandId);
    this.requestedRunId.set(runId);
    if (brandChanged) {
      this.activeRun.set(null);
      this.justFinished.set(null);
      this.loadedRunId = null;
      this.reloadBrand();
    } else {
      this.loadRunData();
    }
  }

  reloadBrand(): void {
    const id = this.brandId();
    this.brand.load(
      this.brands.get(id).pipe(
        tap((brand) => {
          this.brand.set(brand); // viewedRunId() derives from it; set before loading run data
          this.portfolio.upsert(brand);
          this.loadRunData();
          this.watchActive(brand.active_run_id);
        }),
      ),
    );
    this.history.load(this.brands.runs(id));
  }

  private loadRunData(): void {
    const runId = this.viewedRunId();
    if (!runId || (runId === this.loadedRunId && this.run.status() !== 'error')) return;
    this.loadedRunId = runId;
    this.run.load(this.runs.get(runId));
    this.queries.load(this.insights.queries(runId));
    this.recommendations.load(this.insights.recommendations(runId));
  }

  reloadRecommendations(): void {
    const runId = this.viewedRunId();
    if (runId) this.recommendations.load(this.insights.recommendations(runId));
  }

  replaceRecommendation(updated: Recommendation): void {
    const list = this.recommendations.value() ?? [];
    this.recommendations.set(list.map((r) => (r.id === updated.id ? updated : r)));
  }

  start(question: string | null, idempotencyKey: string): Observable<Run> {
    return this.runs.start(this.brandId(), question, idempotencyKey).pipe(tap((run) => this.watchActive(run.id, run)));
  }

  retry(runId: string): Observable<Run> {
    return this.runs.retry(runId).pipe(tap((run) => this.watchActive(run.id, run)));
  }

  private watchActive(runId: string | null, initial?: Run): void {
    if (!runId || this.activeRun()?.id === runId) return;
    this.activeSub?.unsubscribe();
    if (initial) this.activeRun.set(initial);
    this.activeSub = this.tracker.track(runId).subscribe({
      next: (run) => {
        this.activeRun.set(run);
        if (isTerminal(run.status)) {
          this.activeRun.set(null);
          this.justFinished.set(run);
          this.requestedRunId.set(null);
          this.reloadBrand();
        }
      },
      error: () => this.activeRun.set(null),
    });
  }
}
