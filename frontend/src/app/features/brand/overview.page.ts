import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { RunsApi } from '../../core/api/api.services';
import { ChannelName, QueryResult } from '../../core/api/models';
import { ToastService } from '../../core/state/stores';
import { AgoPipe, ScorePipe } from '../../core/util/format';
import { CHANNEL_LABEL, CONTENT_TYPE_LABEL } from '../../core/util/labels';
import { Delta, ShareOfVoice, Sparkline } from '../../shared/charts';
import { DataQualityNote, RunStatusBadge } from '../../shared/run-widgets';
import { StatePanel } from '../../shared/state-panel';
import { VisibilityMap } from '../../shared/visibility-map';
import { BrandWorkspace } from './brand-workspace';
import { OpportunityDrawer } from './opportunity-drawer';

@Component({
  selector: 'app-brand-overview',
  imports: [RouterLink, StatePanel, VisibilityMap, ShareOfVoice, Sparkline, Delta, DataQualityNote, RunStatusBadge,
    OpportunityDrawer, ScorePipe, AgoPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (!ws.viewedRunId()) {
      <app-state kind="empty" title="The first analysis is running"
                 message="Results appear here as soon as it finishes. Follow its progress from the banner above." />
    } @else if (ws.run.initialLoading() || ws.queries.initialLoading()) {
      <app-state kind="loading" [lines]="8" title="Loading results" />
    } @else if (ws.run.status() === 'error' || ws.queries.status() === 'error') {
      <app-state kind="error" title="The results could not be loaded" [error]="ws.run.error() ?? ws.queries.error()" (retry)="ws.reloadBrand()" />
    } @else if (ws.run.value(); as run) {
      @if (run.report; as report) {
        <section class="hero">
          <div class="scorebox">
            <span class="score num" [attr.aria-label]="'Visibility score ' + (report.summary.visibility_score | score) + ' out of 100'">{{ report.summary.visibility_score | score }}<span class="of">/100</span></span>
            <div class="meta">
              <span class="muted">Visibility score</span>
              @if (delta() !== null) {
                <span class="row"><app-delta [value]="delta()" unit=" pts" /> <span class="faint small">since the previous analysis</span></span>
              }
              @if (trend().length > 1) {
                <app-sparkline [values]="trend()" [width]="140" [height]="32" />
              } @else {
                <span class="faint small">Re-measure later to see the trend.</span>
              }
            </div>
          </div>
          <div class="facts">
            <p><app-run-status [status]="run.status" /> Measured {{ run.finished_at | ago }} on {{ measuredCount() }} questions buyers ask.</p>
            @if (report.question) { <p class="muted small">Research question: {{ report.question }}</p> }
            <div class="row exports">
              <span class="faint small">Export</span>
              <a class="btn ghost sm" [href]="exportUrl('csv')" download>CSV</a>
              <a class="btn ghost sm" [href]="exportUrl('md')" download>Report</a>
              <a class="btn ghost sm" [href]="exportUrl('json')" download>JSON</a>
            </div>
          </div>
        </section>

        @if (run.degradations.length) {
          <div class="dq">
            <app-data-quality [degradations]="run.degradations" />
            @if (ws.isLatest() && !ws.activeRun()) {
              <button type="button" class="btn sm" (click)="retry(run.id)" [disabled]="retrying()">{{ retrying() ? 'Retrying…' : 'Retry what failed' }}</button>
            }
          </div>
        }

        <section class="panel map-panel">
            <div class="panel-title">
              <h2>Where {{ run.profile.name }} is visible</h2>
              <a class="small" routerLink="opportunities" [queryParams]="runParam()">All opportunities</a>
            </div>
            @if (queries().length) {
              <app-visibility-map [queries]="queries()" [brand]="run.profile.name" (select)="selected.set($event.id)" />
            } @else {
              <app-state kind="empty" title="No questions were measured" message="See the data quality notes above for what went wrong, then retry." />
            }
        </section>

        <div class="side">
            <section class="panel">
              <h2 class="h">Coverage by channel</h2>
              <ul class="coverage">
                @for (c of coverage(); track c.key) {
                  <li>
                    <span class="name">{{ c.label }}</span>
                    <span class="num"><strong>{{ c.visible }}</strong><span class="faint"> of {{ c.measured }}</span></span>
                    <span class="meter" aria-hidden="true"><span [style.width.%]="c.measured ? (c.visible / c.measured) * 100 : 0"></span></span>
                  </li>
                }
              </ul>
            </section>
            <section class="panel">
              <h2 class="h">Share of voice</h2>
              <app-share-of-voice [entries]="report.summary.share_of_voice" />
            </section>
            @if (report.insights.length) {
              <section class="panel">
                <h2 class="h">What stands out</h2>
                <ul class="insights">
                  @for (i of report.insights; track $index) { <li>{{ i }}</li> }
                </ul>
                <p class="faint small">{{ report.data_quality.analysis_source === 'rule' ? 'Generated by rules: the AI analyst was unavailable.' : 'Written by the AI analyst from the measured data.' }}</p>
              </section>
            }
        </div>

        <section class="panel recs">
          <div class="panel-title">
            <h2>Publish next</h2>
            <a class="small" routerLink="recommendations" [queryParams]="runParam()">All recommendations</a>
          </div>
          @if (topRecs().length) {
            <ol class="rec-list">
              @for (r of topRecs(); track r.id) {
                <li>
                  <span class="title">{{ r.title }}</span>
                  <span class="small muted">{{ contentLabel(r.content_type) }} for “{{ r.query_text }}”</span>
                </li>
              }
            </ol>
          } @else {
            <p class="muted">No gaps were judged worth closing in this analysis.</p>
          }
        </section>
      } @else {
        <app-state kind="error" title="This analysis has no report" [message]="run.error?.message ?? 'It did not finish.'" />
      }
    } @else {
      <app-state kind="loading" [lines]="8" title="Loading results" />
    }
    @if (selected(); as id) {
      <app-opportunity-drawer [queryId]="id" (close)="selected.set(null)" />
    }
  `,
  styles: `
    .hero { display: grid; grid-template-columns: auto 1fr; gap: var(--s-7); align-items: end; margin-bottom: var(--s-6); }
    .scorebox { display: flex; align-items: flex-end; gap: var(--s-4); }
    .score { font: 600 var(--t-hero)/0.8 var(--font-display); letter-spacing: -0.05em; color: var(--signal); }
    .of { font-size: var(--t-lg); letter-spacing: 0; color: var(--ink-3); margin-left: 4px; }
    .meta { display: grid; gap: 6px; padding-bottom: 4px; }
    .facts { display: grid; gap: var(--s-2); justify-items: start; }
    .facts p { display: flex; gap: var(--s-2); align-items: center; flex-wrap: wrap; }
    .exports { gap: var(--s-1); }
    .dq { display: grid; gap: var(--s-3); justify-items: start; margin-bottom: var(--s-5); }
    .map-panel { margin-bottom: var(--s-5); }
    .side { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--s-5); align-items: start; margin-bottom: var(--s-5); }
    .h { font-size: var(--t-md); margin-bottom: var(--s-4); }
    .coverage { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--s-3); }
    .coverage li { display: grid; grid-template-columns: 1fr auto; gap: 4px; font-size: var(--t-sm); }
    .meter { grid-column: 1 / -1; height: 8px; border-radius: 4px; background: var(--hatch), var(--surface-sunk); overflow: hidden; }
    .meter span { display: block; height: 100%; background: var(--signal); }
    .insights { margin: 0 0 var(--s-3); padding-left: 1.1em; display: grid; gap: var(--s-2); font-size: var(--t-sm); }
    .rec-list { margin: 0; padding-left: 1.4em; display: grid; gap: var(--s-3); }
    .rec-list li { display: grid; gap: 2px; }
    .rec-list .title { font-weight: 600; }
    @media (max-width: 1180px) { .side { grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); } }
    @media (max-width: 860px) { .hero { grid-template-columns: 1fr; gap: var(--s-4); } }
  `,
})
export class BrandOverviewPage {
  protected readonly ws = inject(BrandWorkspace);
  private readonly runs = inject(RunsApi);
  private readonly toasts = inject(ToastService);
  protected readonly selected = signal<string | null>(null);
  protected readonly retrying = signal(false);

  protected readonly queries = computed<QueryResult[]>(() => this.ws.queries.value()?.items ?? []);
  protected readonly measuredCount = computed(() => this.queries().filter((q) => q.status !== 'failed').length);
  protected readonly topRecs = computed(() =>
    (this.ws.recommendations.value() ?? []).filter((r) => r.status !== 'dismissed' && r.status !== 'done').slice(0, 3),
  );
  protected readonly runParam = computed(() => (this.ws.isLatest() ? {} : { run: this.ws.viewedRunId() }));
  protected readonly trend = computed(() => (this.ws.brand.value()?.trend ?? []).map((p) => p.visibility_score));
  protected readonly delta = computed(() => {
    const trend = this.ws.brand.value()?.trend ?? [];
    const i = trend.findIndex((p) => p.run_id === this.ws.viewedRunId());
    const cur = trend[i]?.visibility_score;
    const prev = i > 0 ? trend[i - 1]?.visibility_score : null;
    return cur !== null && cur !== undefined && prev !== null && prev !== undefined ? cur - prev : null;
  });
  protected readonly coverage = computed(() => {
    const cov = this.ws.run.value()?.report?.summary.channel_coverage;
    return (Object.keys(CHANNEL_LABEL) as ChannelName[]).map((key) => ({
      key,
      label: CHANNEL_LABEL[key],
      visible: cov?.[key]?.visible ?? 0,
      measured: cov?.[key]?.measured ?? 0,
    }));
  });

  protected exportUrl(format: 'csv' | 'json' | 'md'): string {
    return this.runs.exportUrl(this.ws.viewedRunId() ?? '', format);
  }

  protected contentLabel(type: string): string {
    return CONTENT_TYPE_LABEL[type] ?? type;
  }

  protected retry(runId: string): void {
    this.retrying.set(true);
    this.ws.retry(runId).subscribe({
      next: () => {
        this.retrying.set(false);
        this.toasts.show('Retrying the parts that failed. Completed work is reused.', 'success');
      },
      error: (e: unknown) => {
        this.retrying.set(false);
        this.toasts.error(e);
      },
    });
  }
}
