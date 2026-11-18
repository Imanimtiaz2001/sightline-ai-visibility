import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { NavigationEnd, NavigationError, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ErrorReporter } from '../core/observability/error-reporter';
import { MetaStore, PortfolioStore, ThemeService, ToastService } from '../core/state/stores';
import { ScorePipe } from '../core/util/format';
import { ToastHost } from '../shared/run-widgets';

@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, ScorePipe, ToastHost],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <a class="skip" href="#main">Skip to content</a>
    <div class="layout" [class.nav-open]="navOpen()">
      <aside class="nav" aria-label="Main navigation">
        <div class="brandmark">
          <a routerLink="/" class="wordmark" (click)="navOpen.set(false)">
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
              <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="2" opacity=".35" />
              <circle cx="12" cy="12" r="5.5" fill="none" stroke="currentColor" stroke-width="2" opacity=".7" />
              <circle cx="12" cy="12" r="2" fill="currentColor" />
            </svg>
            Sightline
          </a>
          <button type="button" class="menu btn ghost sm" (click)="navOpen.set(!navOpen())" [attr.aria-expanded]="navOpen()">
            {{ navOpen() ? 'Close' : 'Menu' }}
          </button>
        </div>
        <nav class="links">
          <a routerLink="/" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }" (click)="navOpen.set(false)">Portfolio</a>
          <p class="group">Brands</p>
          @for (b of portfolio.brands(); track b.id) {
            <a [routerLink]="['/brands', b.id]" routerLinkActive="active" class="brand-link" (click)="navOpen.set(false)">
              <span class="name">{{ b.name }}</span>
              @if (b.active_run_id) {
                <span class="live" title="Analysis running" aria-label="Analysis running"></span>
              } @else {
                <span class="score num">{{ b.latest_completed_run?.visibility_score | score }}</span>
              }
            </a>
          } @empty {
            @if (portfolio.loaded()) {
              <p class="empty">No brands yet.</p>
            }
          }
          <a routerLink="/brands/new" class="add" (click)="navOpen.set(false)">+ Add a brand</a>
        </nav>
        <div class="foot">
          <button type="button" class="btn ghost sm theme" (click)="theme.toggle()">
            {{ theme.theme() === 'dark' ? 'Light mode' : 'Dark mode' }}
          </button>
          @if (meta.meta(); as m) {
            <p class="small">{{ m.data_mode === 'live' ? 'Live data' : 'Synthetic data' }}<br />Version {{ m.version }}</p>
          }
        </div>
      </aside>

      <div class="content">
        @if (meta.unreachable()) {
          <div class="banner danger" role="alert">The API is not reachable. Data shown may be out of date; actions will fail until it is back.</div>
        } @else if (problems().length) {
          <div class="banner danger" role="alert">
            Analyses cannot run until the server is configured: {{ problems().join('; ') }}. See the README, “Configuration”.
          </div>
        }
        @if (synthetic()) {
          <div class="banner rival" role="note">
            Synthetic data mode: results come from recorded fixtures, not live search or AI answers. Set DATAFORSEO_MODE=live for real measurements.
          </div>
        }
        <main id="main" tabindex="-1"><router-outlet /></main>
      </div>
    </div>
    <app-toasts />
  `,
  styles: `
    .skip { position: absolute; left: -999px; top: 8px; z-index: 100; background: var(--surface); padding: 8px 12px; }
    .skip:focus { left: 8px; }
    .layout { display: grid; grid-template-columns: 248px minmax(0, 1fr); min-height: 100vh; }
    .nav { background: var(--nav); color: var(--nav-ink); display: flex; flex-direction: column; position: sticky; top: 0; height: 100vh; padding: var(--s-5) var(--s-4); }
    .brandmark { display: flex; justify-content: space-between; align-items: center; padding: 0 var(--s-2) var(--s-5); }
    .wordmark { display: inline-flex; align-items: center; gap: 10px; color: var(--nav-ink-strong); text-decoration: none;
      font: 700 1.3rem/1 var(--font-display); letter-spacing: -0.02em; }
    .wordmark svg { color: #8e98ff; }
    .menu { display: none; color: var(--nav-ink); }
    .links { display: flex; flex-direction: column; gap: 2px; overflow-y: auto; flex: 1; }
    .links a { display: flex; align-items: center; justify-content: space-between; gap: var(--s-2); padding: 8px 10px; border-radius: var(--r-sm);
      color: var(--nav-ink); text-decoration: none; font-size: var(--t-sm); }
    .links a:hover { background: var(--nav-line); color: var(--nav-ink-strong); }
    .links a.active { background: rgba(142, 152, 255, .16); color: var(--nav-ink-strong); font-weight: 600; }
    .group { margin: var(--s-5) 10px var(--s-2); font-size: var(--t-xs); color: #7a82a0; }
    .brand-link .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .brand-link .score { color: #9aa2c0; font-size: var(--t-xs); }
    .live { width: 8px; height: 8px; border-radius: 50%; background: #8e98ff; box-shadow: 0 0 0 4px rgba(142,152,255,.2); }
    .add { margin-top: var(--s-2); color: #aab2ff !important; }
    .empty { font-size: var(--t-sm); padding: 4px 10px; color: #7a82a0; }
    .foot { border-top: 1px solid var(--nav-line); padding-top: var(--s-4); display: grid; gap: var(--s-2); color: #7a82a0; }
    .theme { justify-self: start; color: var(--nav-ink); }
    .content { min-width: 0; }
    main:focus { outline: none; }
    .banner { padding: 10px var(--s-7); font-size: var(--t-sm); font-weight: 500; }
    .banner.danger { background: var(--danger-soft); color: var(--danger); }
    .banner.rival { background: var(--rival-soft); color: var(--ink); border-bottom: 1px solid var(--rival); }
    @media (max-width: 960px) {
      .layout { grid-template-columns: 1fr; grid-template-rows: auto 1fr; }
      .nav { position: sticky; height: auto; padding: var(--s-3) var(--s-4); z-index: 20; }
      .brandmark { padding: 0; }
      .menu { display: inline-flex; }
      .links, .foot { display: none; }
      .nav-open .links, .nav-open .foot { display: flex; padding-top: var(--s-4); }
      .nav-open .foot { display: grid; }
      .banner { padding: 10px var(--s-5); }
    }
  `,
})
export class Shell implements OnInit {
  protected readonly portfolio = inject(PortfolioStore);
  protected readonly meta = inject(MetaStore);
  protected readonly theme = inject(ThemeService);
  protected readonly navOpen = signal(false);
  protected readonly problems = computed(() => this.meta.meta()?.configuration_problems ?? []);
  protected readonly synthetic = computed(() => this.meta.meta()?.synthetic_data ?? false);

  constructor() {
    // A lazy route chunk can fail to load: the user is offline, or their tab predates
    // a deploy. Angular aborts the navigation silently, so the click looks broken.
    // Tell the user, and report it, because missing chunks after a release are a real incident.
    const router = inject(Router);
    const toasts = inject(ToastService);
    const reporter = inject(ErrorReporter);
    router.events.pipe(filter((e): e is NavigationError => e instanceof NavigationError), takeUntilDestroyed())
      .subscribe((e) => {
        toasts.show('That page could not be loaded. Check your connection, then reload the app.', 'error');
        reporter.report({ kind: 'resource', message: `navigation to ${e.url} failed: ${e.error}`, context: { url: e.url } });
      });

    // Move focus to the new page for screen-reader users, but only when the path changes:
    // filters live in the query string and must not steal focus while typing.
    let lastPath = '';
    router
      .events.pipe(
        filter((e): e is NavigationEnd => e instanceof NavigationEnd),
        takeUntilDestroyed(),
      )
      .subscribe((e) => {
        const path = e.urlAfterRedirects.split('?')[0];
        if (lastPath && path !== lastPath) document.getElementById('main')?.focus({ preventScroll: true });
        lastPath = path;
      });
  }

  ngOnInit(): void {
    this.meta.load();
    this.portfolio.refresh();
  }
}
