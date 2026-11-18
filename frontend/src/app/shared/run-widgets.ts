import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';
import { Degradation, RunStatus, StageProgress } from '../core/api/models';
import { ToastService } from '../core/state/stores';
import { DEGRADATION_NODE_LABEL, RUN_STATUS_LABEL, STAGE_STATUS_LABEL } from '../core/util/labels';

@Component({
  selector: 'app-run-status',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="chip" [class]="tone()"><i class="dot" aria-hidden="true"></i>{{ label() }}</span>`,
  styles: `
    .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; display: inline-block; }
    .running .dot, .queued .dot { animation: pulse 1.2s ease-in-out infinite; }
    @keyframes pulse { 50% { opacity: .25; } }
  `,
})
export class RunStatusBadge {
  readonly status = input.required<RunStatus>();
  protected readonly label = computed(() => RUN_STATUS_LABEL[this.status()]);
  protected readonly tone = computed(() => {
    const s = this.status();
    return { succeeded: 'ok', partial: 'rival', failed: 'danger', running: 'signal running', queued: 'queued' }[s];
  });
}

/**
 * The pipeline as the user experiences it: ten plain-language stages, each with
 * its state and what it found. "Done with gaps" is shown in amber, not hidden.
 */
@Component({
  selector: 'app-stage-timeline',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ol class="timeline">
      @for (s of stages(); track s.stage) {
        <li [class]="s.status">
          <span class="marker" aria-hidden="true">
            @switch (s.status) {
              @case ('ok') { ✓ }
              @case ('degraded') { ! }
              @case ('failed') { × }
              @case ('skipped') { – }
              @default { }
            }
          </span>
          <div class="body">
            <div class="row between">
              <span class="label">{{ s.label }}</span>
              <span class="state small">{{ statusLabel(s) }}</span>
            </div>
            @if (s.stage === 'measure' && s.total) {
              <div class="progress" role="progressbar" [attr.aria-valuenow]="s.completed" aria-valuemin="0" [attr.aria-valuemax]="s.total">
                <span [style.width.%]="(s.completed / s.total) * 100"></span>
              </div>
            }
            @if (s.detail) {
              <p class="small muted">{{ s.detail }}</p>
            }
          </div>
        </li>
      }
    </ol>
  `,
  styles: `
    .timeline { list-style: none; margin: 0; padding: 0; display: grid; }
    li { display: grid; grid-template-columns: 28px 1fr; gap: var(--s-3); position: relative; padding-bottom: var(--s-4); }
    li:not(:last-child)::before { content: ''; position: absolute; left: 13px; top: 28px; bottom: 2px; width: 2px; background: var(--line); }
    li.ok:not(:last-child)::before, li.degraded:not(:last-child)::before { background: var(--signal); }
    .marker { width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center; font-size: 13px; font-weight: 800;
      border: 2px solid var(--line-strong); background: var(--surface); color: var(--ink-3); }
    .ok .marker { background: var(--signal); border-color: var(--signal); color: var(--signal-ink); }
    .degraded .marker { background: var(--rival-soft); border-color: var(--rival); color: var(--rival); }
    .failed .marker { background: var(--danger-soft); border-color: var(--danger); color: var(--danger); }
    .running .marker { border-color: var(--signal); border-top-color: transparent; animation: spin 0.9s linear infinite; }
    .skipped { opacity: .6; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .body { display: grid; gap: 4px; padding-top: 3px; }
    .between { justify-content: space-between; }
    .label { font-weight: 600; }
    .pending .label { color: var(--ink-3); font-weight: 500; }
    .state { color: var(--ink-3); }
    .degraded .state { color: var(--rival); font-weight: 600; }
    .failed .state { color: var(--danger); font-weight: 600; }
    .progress { height: 6px; background: var(--surface-sunk); border-radius: 3px; overflow: hidden; max-width: 320px; }
    .progress span { display: block; height: 100%; background: var(--signal); transition: width .4s ease; }
  `,
})
export class StageTimeline {
  readonly stages = input<StageProgress[]>([]);
  protected statusLabel(s: StageProgress): string {
    return STAGE_STATUS_LABEL[s.status];
  }
}

/** What a partial run is missing, in words a marketer understands. */
@Component({
  selector: 'app-data-quality',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (items().length) {
      <div class="dq" role="note">
        <h3>{{ heading() }}</h3>
        <ul>
          @for (d of items(); track $index) {
            <li><strong>{{ nodeLabel(d.node) }}:</strong> {{ d.message }}</li>
          }
        </ul>
        <p class="small muted">Everything else in this report was measured normally. Retrying re-fetches only what failed; completed calls are reused at no cost.</p>
      </div>
    }
  `,
  styles: `
    .dq { border-left: 3px solid var(--rival); background: var(--rival-soft); border-radius: 0 var(--r-md) var(--r-md) 0; padding: var(--s-4) var(--s-5); display: grid; gap: var(--s-2); }
    h3 { font-size: var(--t-base); }
    ul { margin: 0; padding-left: 1.1em; display: grid; gap: 4px; font-size: var(--t-sm); }
  `,
})
export class DataQualityNote {
  readonly degradations = input<Degradation[]>([]);
  protected readonly items = computed(() => this.degradations().slice(0, 8));
  protected readonly heading = computed(() => {
    const n = this.degradations().length;
    return `This analysis is incomplete in ${n} place${n === 1 ? '' : 's'}`;
  });
  protected nodeLabel(node: string): string {
    return DEGRADATION_NODE_LABEL[node] ?? node;
  }
}

@Component({
  selector: 'app-toasts',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="toasts" aria-live="polite">
      @for (t of toasts.toasts(); track t.id) {
        <div class="toast" [class]="t.tone" role="status">
          <span>{{ t.message }}</span>
          @if (t.reference) {
            <span class="small faint">Reference {{ t.reference }}</span>
          }
          <button type="button" class="btn ghost sm" (click)="toasts.dismiss(t.id)" aria-label="Dismiss">Close</button>
        </div>
      }
    </div>
  `,
  styles: `
    .toasts { position: fixed; right: var(--s-5); bottom: var(--s-5); display: grid; gap: var(--s-2); z-index: 50; max-width: 420px; }
    .toast { display: grid; grid-template-columns: 1fr auto; gap: 2px var(--s-3); align-items: center; padding: var(--s-3) var(--s-4);
      background: var(--ink); color: var(--paper); border-radius: var(--r-md); box-shadow: var(--shadow); font-size: var(--t-sm); }
    .toast .faint { grid-column: 1; color: inherit; opacity: .7; }
    .toast button { grid-row: 1 / span 2; grid-column: 2; color: inherit; }
    .toast.error { border-left: 4px solid var(--danger); }
    .toast.success { border-left: 4px solid var(--ok); }
  `,
})
export class ToastHost {
  protected readonly toasts = inject(ToastService);
}
