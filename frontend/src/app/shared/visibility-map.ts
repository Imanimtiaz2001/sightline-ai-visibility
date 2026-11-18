import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { AIEngineChannel, QueryResult } from '../core/api/models';
import { engineLabel } from '../core/util/labels';
import { VolumePipe } from '../core/util/format';

type CellKind = 'owned' | 'weak' | 'rival' | 'open' | 'absent' | 'failed';

interface Cell {
  kind: CellKind;
  text: string;
  aria: string;
}

interface Column {
  key: string;
  label: string;
}

function rivalText(names: string[]): string {
  if (!names.length) return '';
  return names.length === 1 ? names[0] : `${names[0]} +${names.length - 1}`;
}

/**
 * The visibility map: every measured question × every surface, coloured by who
 * owns it. Ultramarine = the brand appears, amber = only competitors appear,
 * hatched = nobody appears yet, grey = not measured. A marketer can see where
 * the brand is and is not visible in one glance, then click a row for evidence.
 */
@Component({
  selector: 'app-visibility-map',
  imports: [VolumePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="legend small" aria-hidden="true">
      <span><i class="sw owned"></i>{{ brand() }} appears</span>
      <span><i class="sw rival"></i>Only competitors appear</span>
      <span><i class="sw open"></i>Nobody appears yet</span>
      <span><i class="sw failed"></i>Not measured</span>
    </div>
    <div class="scroll-x">
      <table class="map">
        <caption class="sr-only">Where {{ brand() }} is visible for each measured question</caption>
        <thead>
          <tr>
            <th scope="col" class="q">Question buyers ask</th>
            @for (c of columns(); track c.key) {
              <th scope="col">{{ c.label }}</th>
            }
          </tr>
        </thead>
        <tbody>
          @for (row of rows(); track row.query.id) {
            <tr tabindex="0" (click)="select.emit(row.query)" (keydown.enter)="select.emit(row.query)"
                [attr.aria-label]="'Open evidence for ' + row.query.text">
              <th scope="row" class="q">
                <span class="text">{{ row.query.text }}</span>
                <span class="faint small num">{{ row.query.search_volume | volume }}/mo</span>
              </th>
              @for (cell of row.cells; track $index) {
                <td><span class="cell" [class]="cell.kind" [attr.aria-label]="cell.aria" [title]="cell.aria">{{ cell.text }}</span></td>
              }
            </tr>
          }
        </tbody>
        <tfoot>
          <tr>
            <th scope="row" class="q faint small">Visible in</th>
            @for (t of totals(); track $index) {
              <td class="small num"><strong>{{ t.visible }}</strong><span class="faint"> / {{ t.measured }}</span></td>
            }
          </tr>
        </tfoot>
      </table>
    </div>
  `,
  styles: `
    .legend { display: flex; flex-wrap: wrap; gap: var(--s-4); color: var(--ink-2); margin-bottom: var(--s-4); }
    .legend span { display: inline-flex; align-items: center; gap: 6px; }
    .sw { width: 14px; height: 14px; border-radius: 3px; display: inline-block; }
    .map { width: 100%; border-collapse: separate; border-spacing: 4px; font-size: var(--t-sm); }
    th { font-weight: 600; color: var(--ink-3); text-align: center; padding: 0 4px 6px; white-space: nowrap; }
    th.q { text-align: left; }
    tbody th.q { color: var(--ink); font-weight: 500; padding: 6px 12px 6px 0; min-width: 220px; max-width: 340px; }
    tbody th.q .text { display: block; }
    tbody tr { cursor: pointer; }
    tbody tr:hover th.q .text, tbody tr:focus-visible th.q .text { text-decoration: underline; text-underline-offset: 3px; }
    tbody tr:focus-visible { outline: 2px solid var(--signal); outline-offset: 2px; }
    td { padding: 0; min-width: 104px; }
    tfoot td { text-align: center; padding-top: 6px; }
    .cell { display: flex; align-items: center; justify-content: center; height: 40px; padding: 0 8px; border-radius: 6px;
      font-weight: 600; font-size: var(--t-xs); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .owned, .sw.owned { background: var(--signal); color: var(--signal-ink); }
    .weak { background: var(--signal-soft); color: var(--signal); }
    .rival, .sw.rival { background: var(--rival-soft); color: var(--rival); box-shadow: inset 0 0 0 1px var(--rival); }
    .open, .sw.open { background: var(--hatch), var(--surface); color: var(--ink-3); box-shadow: inset 0 0 0 1px var(--line); }
    .absent { color: var(--ink-3); background: transparent; box-shadow: inset 0 0 0 1px var(--line); font-weight: 500; }
    .failed, .sw.failed { background: var(--surface-sunk); color: var(--ink-3); font-weight: 500; }
    @media (max-width: 900px) { td { min-width: 84px; } tbody th.q { min-width: 160px; } .cell { font-size: 11px; padding: 0 4px; } }
  `,
})
export class VisibilityMap {
  readonly queries = input<QueryResult[]>([]);
  readonly brand = input('Brand');
  readonly select = output<QueryResult>();

  protected readonly engines = computed(() => {
    const seen = new Set<string>();
    for (const q of this.queries()) for (const e of q.channels.ai_engines) seen.add(e.engine);
    return [...seen];
  });

  protected readonly columns = computed<Column[]>(() => [
    { key: 'organic', label: 'Google search' },
    { key: 'ai_overview', label: 'AI Overview' },
    ...this.engines().map((e) => ({ key: e, label: engineLabel(e) })),
  ]);

  protected readonly rows = computed(() =>
    this.queries().map((query) => ({ query, cells: this.cellsFor(query) })),
  );

  protected readonly totals = computed(() =>
    this.columns().map((_, i) => {
      const cells = this.rows().map((r) => r.cells[i]).filter((c) => c.kind !== 'failed' && c.kind !== 'absent');
      return { measured: cells.length, visible: cells.filter((c) => c.kind === 'owned' || c.kind === 'weak').length };
    }),
  );

  private cellsFor(q: QueryResult): Cell[] {
    const brand = this.brand();
    const o = q.channels.organic;
    let organic: Cell;
    if (o.status !== 'ok') organic = { kind: 'failed', text: 'Not measured', aria: 'Google search not measured' };
    else if (o.brand_position !== null && o.brand_position <= 10)
      organic = { kind: 'owned', text: `#${o.brand_position}`, aria: `${brand} ranks #${o.brand_position} on Google` };
    else if (o.brand_position !== null)
      organic = { kind: 'weak', text: `#${o.brand_position}`, aria: `${brand} ranks #${o.brand_position}, beyond page one` };
    else {
      const rivals = Object.entries(o.competitor_positions).filter(([, p]) => p <= 10).sort((a, b) => a[1] - b[1]).map(([n]) => n);
      organic = rivals.length
        ? { kind: 'rival', text: rivalText(rivals), aria: `${brand} not in the top 10; ${rivals.join(', ')} rank` }
        : { kind: 'open', text: 'Open', aria: `Neither ${brand} nor a tracked competitor ranks in the top 10` };
    }

    const a = q.channels.ai_overview;
    let overview: Cell;
    if (a.status !== 'ok') overview = { kind: 'failed', text: 'Not measured', aria: 'AI Overview not measured' };
    else if (!a.present) overview = { kind: 'absent', text: 'None shown', aria: 'Google shows no AI Overview for this question' };
    else if (a.brand_cited || a.brand_mentioned)
      overview = { kind: 'owned', text: a.brand_cited ? 'Cited' : 'Mentioned', aria: `${brand} is ${a.brand_cited ? 'cited' : 'mentioned'} in the AI Overview` };
    else if (a.competitors_cited.length)
      overview = { kind: 'rival', text: rivalText(a.competitors_cited), aria: `AI Overview cites ${a.competitors_cited.join(', ')}, not ${brand}` };
    else overview = { kind: 'open', text: 'Open', aria: 'AI Overview cites no tracked brand' };

    const byEngine = new Map(q.channels.ai_engines.map((e) => [e.engine, e]));
    const engines = this.engines().map((name) => this.engineCell(byEngine.get(name), name, brand));
    return [organic, overview, ...engines];
  }

  private engineCell(e: AIEngineChannel | undefined, name: string, brand: string): Cell {
    const label = engineLabel(name);
    if (!e || e.status !== 'ok') return { kind: 'failed', text: 'Not measured', aria: `${label} not measured` };
    if (e.brand_mentioned || e.brand_cited) {
      const how = e.brand_mentioned && e.brand_cited ? 'Named + cited' : e.brand_mentioned ? 'Named' : 'Cited';
      return { kind: 'owned', text: how, aria: `${label} ${how.toLowerCase()} ${brand}` };
    }
    if (e.competitors_mentioned.length)
      return { kind: 'rival', text: rivalText(e.competitors_mentioned), aria: `${label} recommends ${e.competitors_mentioned.join(', ')}, not ${brand}` };
    return { kind: 'open', text: 'Open', aria: `${label} names no tracked brand` };
  }
}
