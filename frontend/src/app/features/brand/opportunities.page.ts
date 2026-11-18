import { ChangeDetectionStrategy, Component, computed, effect, inject } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { debounceTime, distinctUntilChanged, map } from 'rxjs';
import { InsightsApi } from '../../core/api/api.services';
import { ChannelName, Intent, QueryFilter, QueryPage, QueryResult } from '../../core/api/models';
import { VolumePipe } from '../../core/util/format';
import { CHANNEL_LABEL, GAP_LABEL, INTENT_LABEL, engineLabel } from '../../core/util/labels';
import { Loadable } from '../../core/util/loadable';
import { ScoreBar } from '../../shared/charts';
import { StatePanel } from '../../shared/state-panel';
import { BrandWorkspace } from './brand-workspace';
import { OpportunityDrawer } from './opportunity-drawer';

const INTENTS: Intent[] = ['commercial', 'transactional', 'informational', 'navigational'];
type Sort = NonNullable<QueryFilter['sort']>;

/**
 * Explore and narrow the opportunities. All filters live in the URL, so a
 * filtered view can be bookmarked or shared, and filtering runs on the server.
 */
@Component({
  selector: 'app-opportunities-page',
  imports: [ReactiveFormsModule, StatePanel, ScoreBar, OpportunityDrawer, VolumePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './opportunities.page.html',
  styleUrl: './opportunities.page.scss',
})
export class OpportunitiesPage {
  protected readonly ws = inject(BrandWorkspace);
  private readonly api = inject(InsightsApi);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  protected readonly page = new Loadable<QueryPage>();

  protected readonly intents = INTENTS;
  protected readonly channels = Object.keys(CHANNEL_LABEL) as ChannelName[];
  protected readonly labels = { intent: INTENT_LABEL, channel: CHANNEL_LABEL, gap: GAP_LABEL };
  protected readonly engineLabel = engineLabel;

  protected readonly filters = inject(FormBuilder).nonNullable.group({
    search: [''],
    intent: [[] as Intent[]],
    missing_in: ['' as ChannelName | ''],
    worth_closing: [false],
    min_volume: [0],
    sort: ['-score' as Sort],
  });

  private readonly query = toSignal(this.route.queryParamMap, { requireSync: true });
  protected readonly selectedId = computed(() => this.query().get('q'));
  protected readonly activeFilterCount = computed(() => {
    const q = this.query();
    return ['search', 'intent', 'missing_in', 'worth_closing', 'min_volume'].filter((k) => q.has(k)).length;
  });

  constructor() {
    // URL -> form (initial load, back/forward).
    this.route.queryParamMap.pipe(takeUntilDestroyed()).subscribe((q) => {
      this.filters.setValue(
        {
          search: q.get('search') ?? '',
          intent: q.getAll('intent').filter((i): i is Intent => INTENTS.includes(i as Intent)),
          missing_in: (this.channels as string[]).includes(q.get('missing_in') ?? '') ? (q.get('missing_in') as ChannelName) : '',
          worth_closing: q.get('worth_closing') === 'true',
          min_volume: Number(q.get('min_volume')) || 0,
          sort: (q.get('sort') as Sort | null) ?? '-score',
        },
        { emitEvent: false },
      );
    });
    // Form -> URL (debounced so typing does not spam navigation).
    this.filters.valueChanges
      .pipe(
        debounceTime(250),
        map(() => this.filters.getRawValue()),
        distinctUntilChanged((a, b) => JSON.stringify(a) === JSON.stringify(b)),
        takeUntilDestroyed(),
      )
      .subscribe((f) => {
        void this.router.navigate([], {
          relativeTo: this.route,
          queryParamsHandling: 'merge',
          replaceUrl: true,
          queryParams: {
            search: f.search.trim() || null,
            intent: f.intent.length ? f.intent : null,
            missing_in: f.missing_in || null,
            worth_closing: f.worth_closing ? 'true' : null,
            min_volume: f.min_volume > 0 ? f.min_volume : null,
            sort: f.sort !== '-score' ? f.sort : null,
          },
        });
      });
    // URL + selected run -> server request (opening the drawer does not refetch the list).
    effect(() => {
      const runId = this.ws.viewedRunId();
      if (runId) this.page.load(this.api.queries(runId, this.filter()));
    });
  }

  private readonly filter = computed<QueryFilter>(
    () => {
      const q = this.query();
      return {
        search: q.get('search') ?? undefined,
        intent: q.getAll('intent') as Intent[],
        missing_in: (q.get('missing_in') as ChannelName | null) ?? undefined,
        worth_closing: q.get('worth_closing') === 'true' ? true : undefined,
        min_volume: Number(q.get('min_volume')) || undefined,
        sort: (q.get('sort') as Sort | null) ?? '-score',
      };
    },
    { equal: (a, b) => JSON.stringify(a) === JSON.stringify(b) },
  );

  protected reload(): void {
    const runId = this.ws.viewedRunId();
    if (runId) this.page.load(this.api.queries(runId, this.filter()));
  }

  protected toggleIntent(intent: Intent): void {
    const current = this.filters.controls.intent.value;
    this.filters.controls.intent.setValue(current.includes(intent) ? current.filter((i) => i !== intent) : [...current, intent]);
  }

  protected clear(): void {
    this.filters.setValue({ search: '', intent: [], missing_in: '', worth_closing: false, min_volume: 0, sort: '-score' });
  }

  protected open(q: QueryResult): void {
    void this.router.navigate([], { relativeTo: this.route, queryParams: { q: q.id }, queryParamsHandling: 'merge' });
  }

  protected closeDrawer(): void {
    void this.router.navigate([], { relativeTo: this.route, queryParams: { q: null }, queryParamsHandling: 'merge' });
  }

  protected channelState(q: QueryResult, channel: ChannelName): 'owned' | 'missing' | 'na' {
    const v = q.score_breakdown.channel_visibility[channel];
    if (v === null || v === undefined) return 'na';
    return v > 0 ? 'owned' : 'missing';
  }

  protected rivals(q: QueryResult): string {
    return (q.analysis?.competitors_winning ?? []).slice(0, 2).join(', ');
  }
}
