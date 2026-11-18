import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, catchError, finalize, of, tap } from 'rxjs';
import { ApiError } from '../api/api-error';
import { BrandsApi, SystemApi } from '../api/api.services';
import { Brand, Location, Meta } from '../api/models';

/**
 * Shared state, deliberately small:
 *
 * - Server state that several screens show at once (the brand list in the sidebar
 *   and on the portfolio, service capabilities) lives in root signal stores with
 *   explicit `refresh()` – no hidden caching, no global action bus.
 * - State that belongs to one brand lives in `BrandWorkspace`, provided by the
 *   brand route, so it is created and destroyed with that part of the app.
 * - Filters and selections live in the URL so views are shareable and survive reloads.
 */
@Injectable({ providedIn: 'root' })
export class PortfolioStore {
  private readonly api = inject(BrandsApi);
  private readonly state = signal<{ brands: Brand[]; loading: boolean; error: ApiError | null; loaded: boolean }>({
    brands: [],
    loading: false,
    error: null,
    loaded: false,
  });

  readonly brands = computed(() => this.state().brands);
  readonly loading = computed(() => this.state().loading);
  readonly error = computed(() => this.state().error);
  readonly loaded = computed(() => this.state().loaded);

  refresh(): void {
    this.state.update((s) => ({ ...s, loading: true, error: null }));
    this.api
      .list()
      .pipe(finalize(() => this.state.update((s) => ({ ...s, loading: false }))))
      .subscribe({
        next: (res) => this.state.update((s) => ({ ...s, brands: res.items, loaded: true })),
        error: (error: ApiError) => this.state.update((s) => ({ ...s, error, loaded: true })),
      });
  }

  upsert(brand: Brand): void {
    this.state.update((s) => {
      const exists = s.brands.some((b) => b.id === brand.id);
      const brands = exists ? s.brands.map((b) => (b.id === brand.id ? brand : b)) : [...s.brands, brand];
      return { ...s, brands: brands.sort((a, b) => a.name.localeCompare(b.name)) };
    });
  }

  remove(id: string): void {
    this.state.update((s) => ({ ...s, brands: s.brands.filter((b) => b.id !== id) }));
  }
}

@Injectable({ providedIn: 'root' })
export class MetaStore {
  private readonly api = inject(SystemApi);
  readonly meta = signal<Meta | null>(null);
  readonly locations = signal<Location[]>([]);
  readonly unreachable = signal(false);

  load(): void {
    this.api.meta().subscribe({
      next: (meta) => {
        this.meta.set(meta);
        this.unreachable.set(false);
      },
      error: () => this.unreachable.set(true),
    });
  }

  loadLocations(): Observable<Location[]> {
    if (this.locations().length) return of(this.locations());
    return this.api.locations().pipe(
      tap((locations) => this.locations.set(locations)),
      catchError(() => of([])),
    );
  }
}

export type ToastTone = 'info' | 'success' | 'error';

export interface Toast {
  id: number;
  tone: ToastTone;
  message: string;
  reference?: string | null;
}

@Injectable({ providedIn: 'root' })
export class ToastService {
  private next = 1;
  readonly toasts = signal<Toast[]>([]);

  show(message: string, tone: ToastTone = 'info', reference?: string | null): void {
    const toast: Toast = { id: this.next++, tone, message, reference };
    this.toasts.update((list) => [...list.slice(-3), toast]);
    setTimeout(() => this.dismiss(toast.id), tone === 'error' ? 9000 : 4500);
  }

  error(error: unknown, fallback = 'Something went wrong.'): void {
    const e = ApiError.from(error);
    this.show(e.userMessage || fallback, 'error', e.requestId);
  }

  dismiss(id: number): void {
    this.toasts.update((list) => list.filter((t) => t.id !== id));
  }
}

export type Theme = 'light' | 'dark';

@Injectable({ providedIn: 'root' })
export class ThemeService {
  readonly theme = signal<Theme>(this.initial());

  constructor() {
    this.apply(this.theme());
  }

  toggle(): void {
    const next: Theme = this.theme() === 'dark' ? 'light' : 'dark';
    this.theme.set(next);
    this.apply(next);
    try {
      localStorage.setItem('aiv.theme', next);
    } catch {
      /* storage unavailable: preference lasts for this visit only */
    }
  }

  private initial(): Theme {
    try {
      const saved = localStorage.getItem('aiv.theme');
      if (saved === 'light' || saved === 'dark') return saved;
    } catch {
      /* ignore */
    }
    return typeof matchMedia !== 'undefined' && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  private apply(theme: Theme): void {
    document.documentElement.dataset['theme'] = theme;
  }
}
