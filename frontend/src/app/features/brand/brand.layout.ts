import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { combineLatest } from 'rxjs';
import { ApiError } from '../../core/api/api-error';
import { newId } from '../../core/observability/ids';
import { MetaStore, ToastService } from '../../core/state/stores';
import { WhenPipe } from '../../core/util/format';
import { engineLabel } from '../../core/util/labels';
import { StatePanel } from '../../shared/state-panel';
import { BrandWorkspace } from './brand-workspace';

@Component({
  selector: 'app-brand-layout',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, ReactiveFormsModule, StatePanel, WhenPipe],
  providers: [BrandWorkspace],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page">
      @if (ws.brand.initialLoading()) {
        <app-state kind="loading" [lines]="6" title="Loading brand" />
      } @else if (ws.brand.status() === 'error' && !ws.brand.value()) {
        <app-state kind="error" title="This brand could not be loaded" [error]="ws.brand.error()" (retry)="ws.reloadBrand()">
          <a actions class="btn ghost" routerLink="/">Back to portfolio</a>
        </app-state>
      } @else if (ws.brand.value(); as brand) {
        <header class="head">
          <div class="title">
            <h1>{{ brand.name }}</h1>
            <p class="muted">{{ brand.market }}<span class="faint"> in {{ brand.location_name }}, measured against {{ competitorNames() }}</span></p>
          </div>
          <div class="row">
            <a class="btn ghost" [routerLink]="['/brands', brand.id, 'edit']">Edit brand</a>
            <button type="button" class="btn primary" (click)="openStart()" [disabled]="!!ws.activeRun() || !canRun()"
                    [title]="canRun() ? '' : 'The server is not configured to run analyses'">
              {{ brand.latest_completed_run ? 'Re-measure' : 'Run first analysis' }}
            </button>
          </div>
        </header>

        @if (startOpen()) {
          <form class="start panel" [formGroup]="startForm" (ngSubmit)="start()">
            <div class="field">
              <label for="question">Research question <span class="faint">(optional)</span></label>
              <input id="question" class="input" [formControl]="question"
                     placeholder="How does {{ brand.name }} show up for 'best project management software'?" />
              <span class="hint">Name a specific search phrase in quotes and it will always be measured. Leave empty to research the whole market.</span>
            </div>
            <p class="small muted">
              Measures up to {{ meta.meta()?.limits?.max_queries_per_run ?? 8 }} questions on Google and
              {{ engines() }}, usually in 1–3 minutes. You can leave this page while it runs.
            </p>
            @if (startError(); as e) {
              <p class="error small" role="alert">{{ e.userMessage }} @if (e.requestId) { (reference {{ e.requestId }}) }</p>
            }
            <div class="row">
              <button type="submit" class="btn primary" [disabled]="starting() || question.invalid">{{ starting() ? 'Starting…' : 'Start analysis' }}</button>
              <button type="button" class="btn ghost" (click)="startOpen.set(false)">Cancel</button>
            </div>
          </form>
        }

        @if (ws.activeRun(); as run) {
          <a class="live panel" [routerLink]="['/runs', run.id]">
            <span class="pulse" aria-hidden="true"></span>
            <span><strong>Analysis in progress.</strong> {{ currentStage() }}</span>
            <span class="track" aria-hidden="true"><span [style.width.%]="progress()"></span></span>
            <span class="link">Watch progress</span>
          </a>
        } @else if (ws.justFinished(); as done) {
          @if (done.status === 'failed') {
            <div class="notice danger" role="alert">
              The latest analysis did not produce results: {{ done.error?.message }}
              <a [routerLink]="['/runs', done.id]">See what happened</a>
            </div>
          }
        }

        @if (ws.hasResults() || ws.activeRun()) {
          <nav class="tabs" aria-label="Brand sections">
            <a routerLink="." [queryParams]="runParam()" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">Overview</a>
            <a routerLink="opportunities" [queryParams]="runParam()" routerLinkActive="active">Opportunities</a>
            <a routerLink="recommendations" [queryParams]="runParam()" routerLinkActive="active">Recommendations</a>
            <a routerLink="history" routerLinkActive="active">History</a>
          </nav>
          @if (!ws.isLatest() && ws.run.value(); as viewed) {
            <div class="notice">
              You are viewing the analysis from {{ viewed.created_at | when }}.
              <a [routerLink]="[]" [queryParams]="{ run: null }" queryParamsHandling="merge">Back to the latest</a>
            </div>
          }
          <router-outlet />
        } @else {
          <app-state kind="empty" title="No analysis yet"
                     message="Run the first analysis to see which questions buyers ask in this market, where the brand appears on Google and in AI answers, and what to publish to close the gaps." />
        }
      }
    </div>
  `,
  styles: `
    .head { display: flex; justify-content: space-between; align-items: flex-end; gap: var(--s-4); flex-wrap: wrap; margin-bottom: var(--s-5); }
    .title h1 { font-size: var(--t-2xl); }
    .title p { margin-top: 6px; }
    .start { display: grid; gap: var(--s-3); margin-bottom: var(--s-5); max-width: 760px; border-color: var(--signal); }
    .error { color: var(--danger); }
    .live { display: grid; grid-template-columns: auto 1fr minmax(120px, 220px) auto; align-items: center; gap: var(--s-4);
      text-decoration: none; color: var(--ink); margin-bottom: var(--s-5); padding: var(--s-4) var(--s-5); border-color: var(--signal); }
    .live .link { color: var(--signal); font-weight: 600; font-size: var(--t-sm); }
    .pulse { width: 10px; height: 10px; border-radius: 50%; background: var(--signal); box-shadow: 0 0 0 0 var(--signal-soft); animation: ring 1.6s infinite; }
    @keyframes ring { 0% { box-shadow: 0 0 0 0 var(--signal-soft); } 100% { box-shadow: 0 0 0 10px transparent; } }
    .track { height: 6px; border-radius: 3px; background: var(--surface-sunk); overflow: hidden; }
    .track span { display: block; height: 100%; background: var(--signal); transition: width .4s; }
    .tabs { display: flex; gap: var(--s-5); border-bottom: 1px solid var(--line); margin-bottom: var(--s-5); overflow-x: auto; }
    .tabs a { padding: var(--s-3) 0; color: var(--ink-2); text-decoration: none; font-weight: 600; font-size: var(--t-sm); border-bottom: 2px solid transparent; margin-bottom: -1px; white-space: nowrap; }
    .tabs a.active { color: var(--ink); border-bottom-color: var(--ink); }
    .notice { padding: var(--s-3) var(--s-4); border-radius: var(--r-md); background: var(--surface-sunk); font-size: var(--t-sm); margin-bottom: var(--s-5); }
    .notice.danger { background: var(--danger-soft); color: var(--danger); }
    @media (max-width: 720px) { .live { grid-template-columns: auto 1fr; } .live .track, .live .link { grid-column: 2; } }
  `,
})
export class BrandLayout {
  protected readonly ws = inject(BrandWorkspace);
  protected readonly meta = inject(MetaStore);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly toasts = inject(ToastService);

  protected readonly startOpen = signal(false);
  protected readonly starting = signal(false);
  protected readonly startError = signal<ApiError | null>(null);
  protected readonly question = new FormControl('', { nonNullable: true, validators: [Validators.maxLength(300)] });
  protected readonly startForm = new FormGroup({ question: this.question });
  /** One key per "start" intent: double submits or retried requests reuse it. */
  private idempotencyKey = newId();

  protected readonly canRun = computed(() => this.meta.meta()?.configured ?? true);
  protected readonly engines = computed(() => {
    const names = (this.meta.meta()?.ai_engines ?? []).map((e) => engineLabel(e.engine));
    return names.length ? names.join(' and ') : 'AI assistants';
  });
  protected readonly competitorNames = computed(() => {
    const names = this.ws.brand.value()?.competitors.map((c) => c.name) ?? [];
    return names.length ? names.join(', ') : 'no competitors yet';
  });
  protected readonly runParam = computed(() => (this.ws.isLatest() ? {} : { run: this.ws.viewedRunId() }));
  protected readonly currentStage = computed(() => {
    const stages = this.ws.activeRun()?.stages ?? [];
    const current = stages.find((s) => s.status === 'running') ?? stages.find((s) => s.status === 'pending');
    return current ? `${current.label}${current.detail ? ` – ${current.detail}` : ''}` : 'Starting…';
  });
  protected readonly progress = computed(() => {
    const stages = this.ws.activeRun()?.stages ?? [];
    const done = stages.filter((s) => s.status !== 'pending' && s.status !== 'running').length;
    return stages.length ? Math.max(4, (done / stages.length) * 100) : 4;
  });

  constructor() {
    combineLatest([this.route.paramMap, this.route.queryParamMap])
      .pipe(takeUntilDestroyed())
      .subscribe(([params, query]) => this.ws.open(params.get('id') ?? '', query.get('run')));
  }

  protected openStart(): void {
    this.idempotencyKey = newId();
    this.startError.set(null);
    this.startOpen.set(true);
  }

  protected start(): void {
    if (this.starting()) return;
    this.starting.set(true);
    this.startError.set(null);
    const q = this.question.value.trim();
    this.ws.start(q.length >= 5 ? q : null, this.idempotencyKey).subscribe({
      next: (run) => {
        this.starting.set(false);
        this.startOpen.set(false);
        this.question.setValue('');
        this.toasts.show('Analysis started', 'success');
        void this.router.navigate(['/runs', run.id]);
      },
      error: (e: unknown) => {
        this.starting.set(false);
        this.startError.set(ApiError.from(e));
      },
    });
  }
}
