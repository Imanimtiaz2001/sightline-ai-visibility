import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, input, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';
import { RunsApi } from '../../core/api/api.services';
import { Run } from '../../core/api/models';
import { APP_CONFIG } from '../../core/config/app-config';
import { RunTracker } from '../../core/runs/run-tracker';
import { PortfolioStore, ToastService } from '../../core/state/stores';
import { DurationPipe, ScorePipe, WhenPipe } from '../../core/util/format';
import { isTerminal } from '../../core/util/labels';
import { DataQualityNote, RunStatusBadge, StageTimeline } from '../../shared/run-widgets';
import { StatePanel } from '../../shared/state-panel';

/**
 * A run while it happens and after: plain-language stages with live progress,
 * what is missing if it only partly worked, and the ids an engineer needs.
 */
@Component({
  selector: 'app-run-page',
  imports: [RouterLink, StatePanel, StageTimeline, RunStatusBadge, DataQualityNote, DurationPipe, ScorePipe, WhenPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './run.page.html',
  styleUrl: './run.page.scss',
})
export class RunPage {
  readonly id = input.required<string>();

  private readonly tracker = inject(RunTracker);
  private readonly api = inject(RunsApi);
  private readonly router = inject(Router);
  private readonly toasts = inject(ToastService);
  private readonly portfolio = inject(PortfolioStore);
  private readonly apiBase = inject(APP_CONFIG).apiBaseUrl;

  protected readonly run = signal<Run | null>(null);
  protected readonly error = signal<unknown>(null);
  protected readonly retrying = signal(false);
  private readonly now = signal(Date.now());
  private sub: Subscription | null = null;

  protected readonly terminal = computed(() => {
    const run = this.run();
    return run ? isTerminal(run.status) : false;
  });
  protected readonly elapsed = computed(() => {
    const run = this.run();
    if (!run) return null;
    if (run.duration_s !== null) return run.duration_s;
    const start = run.started_at ?? run.created_at;
    return Math.max(0, (this.now() - new Date(start).getTime()) / 1000);
  });
  protected readonly traceUrl = computed(() => `${this.apiBase}/runs/${this.id()}/trace`);
  protected readonly resultsLink = computed(() => {
    const run = this.run();
    return run ? ['/brands', run.brand_id] : ['/'];
  });

  constructor() {
    const timer = setInterval(() => this.now.set(Date.now()), 1000);
    inject(DestroyRef).onDestroy(() => {
      clearInterval(timer);
      this.sub?.unsubscribe();
    });
    effect(() => this.follow(this.id()));
  }

  protected follow(id: string): void {
    this.sub?.unsubscribe();
    this.error.set(null);
    let wasActive = false;
    this.sub = this.tracker.track(id).subscribe({
      next: (run) => {
        if (!isTerminal(run.status)) wasActive = true;
        else if (wasActive) this.announce(run);
        this.run.set(run);
      },
      error: (e: unknown) => this.error.set(e),
    });
  }

  protected retry(): void {
    const run = this.run();
    if (!run || this.retrying()) return;
    this.retrying.set(true);
    this.api.retry(run.id).subscribe({
      next: (next) => {
        this.retrying.set(false);
        void this.router.navigate(['/runs', next.id]);
      },
      error: (e: unknown) => {
        this.retrying.set(false);
        this.toasts.error(e);
      },
    });
  }

  private announce(run: Run): void {
    this.portfolio.refresh();
    const message = { succeeded: 'Analysis complete', partial: 'Analysis finished with some gaps', failed: 'Analysis failed' }[
      run.status as 'succeeded' | 'partial' | 'failed'
    ];
    this.toasts.show(message, run.status === 'failed' ? 'error' : 'success');
  }
}
