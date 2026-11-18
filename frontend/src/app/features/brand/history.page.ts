import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { RunsApi } from '../../core/api/api.services';
import { RunBrief, RunComparison } from '../../core/api/models';
import { PercentPipe, ScorePipe, WhenPipe } from '../../core/util/format';
import { Loadable } from '../../core/util/loadable';
import { Delta } from '../../shared/charts';
import { RunStatusBadge } from '../../shared/run-widgets';
import { StatePanel } from '../../shared/state-panel';
import { BrandWorkspace } from './brand-workspace';

const CHANGE_LABEL: Record<string, string> = {
  improved: 'Improved',
  declined: 'Declined',
  unchanged: 'No real change',
  new: 'Newly measured',
  dropped: 'No longer measured',
};

@Component({
  selector: 'app-history-page',
  imports: [RouterLink, StatePanel, RunStatusBadge, Delta, WhenPipe, ScorePipe, PercentPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './history.page.html',
  styleUrl: './history.page.scss',
})
export class HistoryPage {
  protected readonly ws = inject(BrandWorkspace);
  private readonly runs = inject(RunsApi);
  protected readonly comparison = new Loadable<RunComparison>();
  protected readonly changeLabel = CHANGE_LABEL;

  protected readonly completed = computed(() =>
    (this.ws.history.value()?.items ?? []).filter((r) => r.status === 'succeeded' || r.status === 'partial'),
  );
  protected readonly currentId = signal<string | null>(null);
  protected readonly baselineId = signal<string | null>(null);

  /** Chart geometry: oldest run on the left. */
  protected readonly chart = computed(() => {
    const points = [...this.completed()].reverse().filter((r) => r.visibility_score !== null);
    const w = 640;
    const h = 180;
    const pad = { l: 36, r: 16, t: 12, b: 28 };
    const step = points.length > 1 ? (w - pad.l - pad.r) / (points.length - 1) : 0;
    const xy = points.map((r, i) => ({
      run: r,
      x: pad.l + (points.length > 1 ? i * step : (w - pad.l - pad.r) / 2),
      y: pad.t + (1 - (r.visibility_score ?? 0) / 100) * (h - pad.t - pad.b),
    }));
    return {
      w,
      h,
      pad,
      xy,
      path: xy.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' '),
      grid: [0, 25, 50, 75, 100].map((v) => ({ v, y: pad.t + (1 - v / 100) * (h - pad.t - pad.b) })),
    };
  });

  constructor() {
    // Default comparison: the latest completed run against the one before it.
    effect(() => {
      const runs = this.completed();
      if (!this.currentId() && runs.length) this.currentId.set(runs[0].id);
      if (!this.baselineId() && runs.length > 1) this.baselineId.set(runs[1].id);
    });
    effect(() => this.loadComparison());
  }

  protected loadComparison(): void {
    const current = this.currentId();
    if (current && this.completed().length > 1) {
      this.comparison.load(this.runs.compare(current, this.baselineId() ?? undefined));
    }
  }

  protected pick(kind: 'current' | 'baseline', event: Event): void {
    const value = (event.target as HTMLSelectElement).value || null;
    (kind === 'current' ? this.currentId : this.baselineId).set(value);
  }

  protected label(run: RunBrief): string {
    const date = new Date(run.created_at).toLocaleString('en', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
    return `${date}, score ${run.visibility_score === null ? '–' : Math.round(run.visibility_score)}`;
  }

  protected pctDelta(v: number | null): number | null {
    return v === null ? null : Math.round(v * 100);
  }
}
