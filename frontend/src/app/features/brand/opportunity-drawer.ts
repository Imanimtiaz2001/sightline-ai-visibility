import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  output,
  viewChild,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { InsightsApi } from '../../core/api/api.services';
import { QueryDetail } from '../../core/api/models';
import { PercentPipe, ScorePipe, VolumePipe, WhenPipe } from '../../core/util/format';
import { CONTENT_TYPE_LABEL, FACTOR_LABEL, GAP_LABEL, INTENT_LABEL, engineLabel } from '../../core/util/labels';
import { Loadable } from '../../core/util/loadable';
import { Sparkline } from '../../shared/charts';
import { StatePanel } from '../../shared/state-panel';
import { BrandWorkspace } from './brand-workspace';

interface Segment {
  text: string;
  kind: 'plain' | 'brand' | 'rival';
}

function escape(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Split text into plain / brand / competitor segments – rendered as text nodes, never as HTML. */
export function highlight(text: string, brand: string, rivals: string[]): Segment[] {
  const names = [brand, ...rivals].filter(Boolean).sort((a, b) => b.length - a.length);
  if (!text || !names.length) return [{ text, kind: 'plain' }];
  const pattern = new RegExp(`(${names.map(escape).join('|')})`, 'gi');
  const lowerBrand = brand.toLowerCase();
  return text
    .split(pattern)
    .filter((part) => part !== '')
    .map((part) => {
      const lower = part.toLowerCase();
      if (lower === lowerBrand) return { text: part, kind: 'brand' as const };
      if (rivals.some((r) => r.toLowerCase() === lower)) return { text: part, kind: 'rival' as const };
      return { text: part, kind: 'plain' as const };
    });
}

@Component({
  selector: 'app-opportunity-drawer',
  imports: [StatePanel, Sparkline, RouterLink, VolumePipe, PercentPipe, ScorePipe, WhenPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(document:keydown.escape)': 'close.emit()' },
  templateUrl: './opportunity-drawer.html',
  styleUrl: './opportunity-drawer.scss',
})
export class OpportunityDrawer {
  readonly queryId = input.required<string>();
  readonly close = output<void>();

  private readonly api = inject(InsightsApi);
  protected readonly ws = inject(BrandWorkspace);
  protected readonly detail = new Loadable<QueryDetail>();
  private readonly closeButton = viewChild<ElementRef<HTMLButtonElement>>('closeButton');

  protected readonly labels = { intent: INTENT_LABEL, gap: GAP_LABEL, factor: FACTOR_LABEL, content: CONTENT_TYPE_LABEL };
  protected readonly engineLabel = engineLabel;
  protected readonly confidenceLabel = { low: 'Low confidence', medium: 'Medium confidence', high: 'High confidence' };
  protected readonly brandName = computed(() => this.ws.brand.value()?.name ?? '');
  protected readonly brandDomain = computed(() => this.ws.brand.value()?.domain ?? '');
  protected readonly rivals = computed(() => this.ws.run.value()?.profile.competitors ?? this.ws.brand.value()?.competitors ?? []);
  protected readonly rivalNames = computed(() => this.rivals().map((c) => c.name));
  protected readonly history = computed(() => (this.detail.value()?.history ?? []).map((h) => (h.visibility === null ? null : h.visibility * 100)));
  protected readonly linkedRecs = computed(() => {
    const ids = new Set(this.detail.value()?.recommendation_ids ?? []);
    return (this.ws.recommendations.value() ?? []).filter((r) => ids.has(r.id));
  });

  constructor() {
    effect(() => {
      this.detail.load(this.api.query(this.queryId()));
    });
    effect(() => {
      if (this.detail.status() === 'ready') queueMicrotask(() => this.closeButton()?.nativeElement.focus());
    });
  }

  protected segments(text: string): Segment[] {
    return highlight(text, this.brandName(), this.rivalNames());
  }

  protected domainKind(domain: string): 'brand' | 'rival' | 'plain' {
    const matches = (owned: string) => domain === owned || domain.endsWith(`.${owned}`);
    if (matches(this.brandDomain())) return 'brand';
    return this.rivals().some((c) => matches(c.domain)) ? 'rival' : 'plain';
  }

  protected reload(): void {
    this.detail.load(this.api.query(this.queryId()));
  }
}
