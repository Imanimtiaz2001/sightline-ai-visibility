import { ChangeDetectionStrategy, Component, OnInit, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SystemApi } from '../../core/api/api.services';
import { BrandOverview, Overview } from '../../core/api/models';
import { AgoPipe, ScorePipe } from '../../core/util/format';
import { Loadable } from '../../core/util/loadable';
import { Delta, Sparkline } from '../../shared/charts';
import { RunStatusBadge } from '../../shared/run-widgets';
import { StatePanel } from '../../shared/state-panel';

/** Every tracked brand on one screen: who is visible, who is moving, where work is waiting. */
@Component({
  selector: 'app-portfolio-page',
  imports: [RouterLink, StatePanel, Sparkline, Delta, ScorePipe, AgoPipe, RunStatusBadge],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page">
      <header class="page-head">
        <div>
          <h1>Portfolio</h1>
          <p class="sub">How visible each brand you track is across Google and AI assistants.</p>
        </div>
        <a class="btn primary" routerLink="/brands/new">Add a brand</a>
      </header>

      @if (overview.initialLoading()) {
        <app-state kind="loading" [lines]="5" title="Loading portfolio" />
      } @else if (overview.status() === 'error' && !overview.value()) {
        <app-state kind="error" title="The portfolio could not be loaded" [error]="overview.error()" (retry)="reload()" />
      } @else if (overview.value(); as o) {
        @if (!o.brands.length) {
          <app-state kind="empty" title="Track your first brand"
            message="Add a brand and the competitors it is measured against. Sightline finds the questions buyers ask, checks who Google and AI assistants show for each, and tells you what to publish.">
            <a actions class="btn primary" routerLink="/brands/new">Add a brand</a>
          </app-state>
        } @else {
          <section class="totals" aria-label="Portfolio totals">
            <div><span class="big num">{{ o.totals.average_visibility | score }}</span><span class="muted">Average visibility, out of 100</span></div>
            <div><span class="big num">{{ o.totals.brands }}</span><span class="muted">Brands tracked</span></div>
            <div><span class="big num">{{ o.totals.open_recommendations }}</span><span class="muted">Recommendations to act on</span></div>
            <div><span class="big num">{{ o.totals.active_runs }}</span><span class="muted">Analyses running now</span></div>
          </section>

          <div class="panel list">
            <div class="scroll-x">
              <table class="table">
                <thead>
                  <tr>
                    <th>Brand</th>
                    <th class="right">Visibility</th>
                    <th>Trend</th>
                    <th>Biggest opportunity</th>
                    <th class="right">To do</th>
                    <th>Last measured</th>
                  </tr>
                </thead>
                <tbody>
                  @for (item of sorted(); track item.brand.id) {
                    <tr class="clickable" [routerLink]="['/brands', item.brand.id]" tabindex="0">
                      <td>
                        <a class="name" [routerLink]="['/brands', item.brand.id]">{{ item.brand.name }}</a>
                        <div class="faint small">{{ item.brand.domain }} – {{ item.brand.market }}</div>
                      </td>
                      <td class="right">
                        <span class="score num">{{ item.brand.latest_completed_run?.visibility_score | score }}</span>
                        <app-delta [value]="item.visibility_delta" />
                      </td>
                      <td><app-sparkline [values]="trend(item)" /></td>
                      <td>
                        @if (item.top_opportunity; as top) {
                          <span>{{ top.query }}</span> <span class="faint small num">score {{ top.score | score }}</span>
                        } @else {
                          <span class="faint">–</span>
                        }
                      </td>
                      <td class="right num">{{ item.open_recommendations }}</td>
                      <td>
                        @if (item.brand.active_run_id) {
                          <app-run-status status="running" />
                        } @else if (item.brand.latest_run; as run) {
                          <span class="small">{{ run.finished_at | ago }}</span>
                          @if (run.status !== 'succeeded') { <app-run-status [status]="run.status" /> }
                        } @else {
                          <span class="chip">Not measured yet</span>
                        }
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          </div>
        }
      }
    </div>
  `,
  styles: `
    .totals { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--s-5); margin-bottom: var(--s-6); }
    .totals div { display: grid; gap: 2px; border-top: 2px solid var(--ink); padding-top: var(--s-3); }
    .totals div:first-child { border-top-color: var(--signal); }
    .big { font: 600 var(--t-2xl)/1 var(--font-display); letter-spacing: -0.03em; }
    .list { padding: var(--s-4) var(--s-3); }
    .name { font-weight: 700; color: var(--ink); text-decoration: none; font-size: var(--t-base); }
    .score { font: 600 var(--t-lg)/1 var(--font-display); margin-right: 6px; }
    td .chip { margin-left: 6px; }
    @media (max-width: 900px) { .totals { grid-template-columns: repeat(2, 1fr); } }
  `,
})
export class PortfolioPage implements OnInit {
  private readonly api = inject(SystemApi);
  protected readonly overview = new Loadable<Overview>();
  protected readonly sorted = computed(() =>
    [...(this.overview.value()?.brands ?? [])].sort(
      (a, b) => (b.top_opportunity?.score ?? -1) - (a.top_opportunity?.score ?? -1),
    ),
  );

  ngOnInit(): void {
    this.reload();
  }

  protected reload(): void {
    this.overview.load(this.api.overview());
  }

  protected trend(item: BrandOverview): (number | null)[] {
    return item.brand.trend.map((p) => p.visibility_score);
  }
}
