import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { ShareOfVoiceEntry } from '../core/api/models';
import { formatPercent } from '../core/util/format';

/** Trend line of visibility over runs. Null points (failed runs) leave a gap rather than a fake zero. */
@Component({
  selector: 'app-sparkline',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <svg [attr.viewBox]="'0 0 ' + width() + ' ' + height()" [attr.width]="width()" [attr.height]="height()"
         role="img" [attr.aria-label]="label()">
      @if (points().length > 1) {
        <path [attr.d]="path()" fill="none" stroke="var(--signal)" stroke-width="1.75" stroke-linejoin="round" stroke-linecap="round" />
      }
      @if (last(); as p) {
        <circle [attr.cx]="p.x" [attr.cy]="p.y" r="3" fill="var(--signal)" />
      }
    </svg>
  `,
  styles: `:host { display: inline-block; line-height: 0; }`,
})
export class Sparkline {
  readonly values = input<(number | null)[]>([]);
  readonly width = input(96);
  readonly height = input(28);
  readonly max = input(100);

  protected readonly points = computed(() => {
    const values = this.values();
    const step = values.length > 1 ? (this.width() - 6) / (values.length - 1) : 0;
    return values
      .map((v, i) => (v === null ? null : { x: 3 + i * step, y: 3 + (1 - v / this.max()) * (this.height() - 6) }))
      .filter((p): p is { x: number; y: number } => p !== null);
  });
  protected readonly path = computed(() =>
    this.points()
      .map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
      .join(' '),
  );
  protected readonly last = computed(() => this.points().at(-1) ?? null);
  protected readonly label = computed(() => {
    const v = this.values().filter((x): x is number => x !== null);
    return v.length ? `Visibility trend, latest ${Math.round(v.at(-1) ?? 0)} of 100 across ${v.length} runs` : 'No trend yet';
  });
}

/** Opportunity score as a short bar – length encodes the number next to it. */
@Component({
  selector: 'app-score-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span class="value num">{{ rounded() }}</span>
    <span class="track" aria-hidden="true"><span class="fill" [style.width.%]="clamped()"></span></span>
  `,
  styles: `
    :host { display: inline-flex; align-items: center; gap: var(--s-2); min-width: 96px; }
    .value { width: 2ch; text-align: right; font-weight: 700; }
    .track { flex: 1; height: 6px; border-radius: 3px; background: var(--surface-sunk); overflow: hidden; min-width: 48px; }
    .fill { display: block; height: 100%; background: var(--ink); border-radius: 3px; }
  `,
})
export class ScoreBar {
  readonly score = input(0);
  protected readonly clamped = computed(() => Math.max(2, Math.min(100, this.score())));
  protected readonly rounded = computed(() => Math.round(this.score()));
}

/** Share of voice: brand in ultramarine, competitors in amber, sorted by share. */
@Component({
  selector: 'app-share-of-voice',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ol class="sov">
      @for (e of sorted(); track e.name) {
        <li [class.brand]="e.is_brand">
          <span class="name">{{ e.name }}</span>
          <span class="bar"><span class="fill" [style.width.%]="width(e)"></span></span>
          <span class="pct num">{{ pct(e.share) }}</span>
        </li>
      }
    </ol>
    <p class="faint small">Share of all brand appearances across {{ surfaces() }} measured search results and AI answers.</p>
  `,
  styles: `
    .sov { list-style: none; margin: 0 0 var(--s-3); padding: 0; display: grid; gap: var(--s-2); }
    li { display: grid; grid-template-columns: minmax(80px, 120px) 1fr 3.2em; align-items: center; gap: var(--s-3); font-size: var(--t-sm); }
    .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .brand .name { font-weight: 700; }
    .bar { height: 10px; background: var(--surface-sunk); border-radius: 5px; overflow: hidden; }
    .fill { display: block; height: 100%; background: var(--rival); border-radius: 5px; }
    .brand .fill { background: var(--signal); }
    .pct { text-align: right; color: var(--ink-2); }
  `,
})
export class ShareOfVoice {
  readonly entries = input<ShareOfVoiceEntry[]>([]);
  protected readonly sorted = computed(() => [...this.entries()].sort((a, b) => b.share - a.share));
  protected readonly surfaces = computed(() => this.entries()[0]?.surfaces ?? 0);
  private readonly maxShare = computed(() => Math.max(0.01, ...this.entries().map((e) => e.share)));
  protected width(e: ShareOfVoiceEntry): number {
    return Math.max(1.5, (e.share / this.maxShare()) * 100);
  }
  protected pct = formatPercent;
}

/** Signed change with direction made explicit in words, not only colour. */
@Component({
  selector: 'app-delta',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (value() !== null) {
      <span class="delta num" [class.up]="(value() ?? 0) > 0" [class.down]="(value() ?? 0) < 0"
            [attr.aria-label]="aria()">{{ text() }}</span>
    }
  `,
  styles: `
    .delta { font-size: var(--t-xs); font-weight: 700; padding: 1px 6px; border-radius: 4px; background: var(--surface-sunk); color: var(--ink-2); }
    .up { background: var(--ok-soft); color: var(--ok); }
    .down { background: var(--danger-soft); color: var(--danger); }
  `,
})
export class Delta {
  readonly value = input<number | null>(null);
  readonly unit = input('');
  protected readonly text = computed(() => {
    const v = this.value() ?? 0;
    const rounded = Math.round(v * 10) / 10;
    return `${rounded > 0 ? '▲ +' : rounded < 0 ? '▼ ' : '± '}${rounded}${this.unit()}`;
  });
  protected readonly aria = computed(() => {
    const v = this.value() ?? 0;
    return v > 0 ? `up ${v}${this.unit()}` : v < 0 ? `down ${Math.abs(v)}${this.unit()}` : 'no change';
  });
}
