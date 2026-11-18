import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { InsightsApi } from '../../core/api/api.services';
import { Recommendation, RecommendationStatus } from '../../core/api/models';
import { ToastService } from '../../core/state/stores';
import { ScorePipe } from '../../core/util/format';
import { CHANNEL_LABEL, CONTENT_TYPE_LABEL } from '../../core/util/labels';
import { StatePanel } from '../../shared/state-panel';
import { BrandWorkspace } from './brand-workspace';

type View = 'todo' | 'done' | 'dismissed' | 'all';

const VIEW_STATUSES: Record<View, RecommendationStatus[]> = {
  todo: ['open', 'in_progress'],
  done: ['done'],
  dismissed: ['dismissed'],
  all: ['open', 'in_progress', 'done', 'dismissed'],
};

export function briefMarkdown(r: Recommendation, brand: string): string {
  return [
    `# ${r.title}`,
    '',
    `**Brand:** ${brand}  `,
    `**Target question:** ${r.query_text}  `,
    `**Format:** ${CONTENT_TYPE_LABEL[r.content_type] ?? r.content_type}  `,
    `**Aim to appear in:** ${r.target_channels.map((c) => CHANNEL_LABEL[c]).join(', ')}  `,
    `**Effort:** ${r.effort}`,
    '',
    '## Why',
    r.rationale,
    '',
    '## Outline',
    ...r.outline.map((h, i) => `${i + 1}. ${h}`),
    '',
    '## Must cover',
    ...r.key_points.map((p) => `- ${p}`),
    ...(r.sources_to_earn.length ? ['', '## Get mentioned on', ...r.sources_to_earn.map((d) => `- ${d}`)] : []),
    '',
  ].join('\n');
}

@Component({
  selector: 'app-recommendations-page',
  imports: [StatePanel, ScorePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './recommendations.page.html',
  styleUrl: './recommendations.page.scss',
})
export class RecommendationsPage {
  protected readonly ws = inject(BrandWorkspace);
  private readonly api = inject(InsightsApi);
  private readonly toasts = inject(ToastService);
  private readonly query = toSignal(inject(ActivatedRoute).queryParamMap, { requireSync: true });

  protected readonly view = signal<View>('todo');
  protected readonly expanded = signal<Set<string>>(new Set());
  protected readonly pending = signal<Set<string>>(new Set());
  protected readonly labels = { content: CONTENT_TYPE_LABEL, channel: CHANNEL_LABEL };
  protected readonly effortLabel = { low: 'Low effort', medium: 'Medium effort', high: 'High effort' };
  protected readonly views: { key: View; label: string }[] = [
    { key: 'todo', label: 'To do' },
    { key: 'done', label: 'Done' },
    { key: 'dismissed', label: 'Dismissed' },
    { key: 'all', label: 'All' },
  ];

  protected readonly all = computed(() => this.ws.recommendations.value() ?? []);
  protected readonly visible = computed(() => this.all().filter((r) => VIEW_STATUSES[this.view()].includes(r.status)));
  protected readonly counts = computed(() => {
    const out: Record<View, number> = { todo: 0, done: 0, dismissed: 0, all: this.all().length };
    for (const r of this.all()) {
      if (r.status === 'done') out.done++;
      else if (r.status === 'dismissed') out.dismissed++;
      else out.todo++;
    }
    return out;
  });
  protected readonly focus = computed(() => this.query().get('focus'));

  constructor() {
    effect(() => {
      const id = this.focus();
      if (!id) return;
      this.expanded.update((s) => new Set([...s, id]));
      queueMicrotask(() => document.getElementById(`rec-${id}`)?.scrollIntoView({ block: 'center' }));
    });
  }

  protected toggle(id: string): void {
    this.expanded.update((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  protected setStatus(r: Recommendation, status: RecommendationStatus): void {
    this.pending.update((s) => new Set([...s, r.id]));
    this.ws.replaceRecommendation({ ...r, status }); // optimistic; reverted on failure
    this.api.setRecommendationStatus(r.id, status).subscribe({
      next: (updated) => {
        this.ws.replaceRecommendation(updated);
        this.done(r.id);
        this.toasts.show(
          { in_progress: 'Marked as in progress', done: 'Marked as done', dismissed: 'Dismissed', open: 'Moved back to to-do' }[status],
          'success',
        );
      },
      error: (e: unknown) => {
        this.ws.replaceRecommendation(r);
        this.done(r.id);
        this.toasts.error(e);
      },
    });
  }

  protected async copy(r: Recommendation): Promise<void> {
    try {
      await navigator.clipboard.writeText(briefMarkdown(r, this.ws.brand.value()?.name ?? ''));
      this.toasts.show('Brief copied to the clipboard', 'success');
    } catch {
      this.toasts.show('Copying is blocked by the browser. Use Download brief instead.', 'error');
    }
  }

  protected download(r: Recommendation): void {
    const blob = new Blob([briefMarkdown(r, this.ws.brand.value()?.name ?? '')], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${r.title.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 60)}.md`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  private done(id: string): void {
    this.pending.update((s) => {
      const next = new Set(s);
      next.delete(id);
      return next;
    });
  }
}
