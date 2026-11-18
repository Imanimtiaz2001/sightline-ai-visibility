import { DestroyRef, Signal, computed, inject, signal } from '@angular/core';
import { Observable, Subscription } from 'rxjs';
import { ApiError } from '../api/api-error';
import { ToastService } from '../state/stores';

export type LoadStatus = 'idle' | 'loading' | 'ready' | 'error';

/**
 * Async value as signals: `value`, `status`, `error`. `load()` cancels the
 * previous request (latest wins) and the subscription dies with the component.
 * Keeping the last value while reloading avoids flashing skeletons on refresh;
 * when such a refresh fails the screen would otherwise keep showing stale data
 * silently, so the failure is announced in a toast instead.
 * Must be created in an injection context.
 */
export class Loadable<T> {
  private readonly state = signal<{ status: LoadStatus; value: T | null; error: unknown }>({
    status: 'idle',
    value: null,
    error: null,
  });
  private sub: Subscription | null = null;
  private readonly toasts = inject(ToastService);

  readonly value: Signal<T | null> = computed(() => this.state().value);
  readonly status: Signal<LoadStatus> = computed(() => this.state().status);
  readonly error: Signal<unknown> = computed(() => this.state().error);
  /** True only for the first load (no value yet) – reloads keep showing the old value. */
  readonly initialLoading: Signal<boolean> = computed(() => this.state().status === 'loading' && this.state().value === null);

  constructor() {
    inject(DestroyRef).onDestroy(() => this.sub?.unsubscribe());
  }

  load(source: Observable<T>): void {
    this.sub?.unsubscribe();
    this.state.update((s) => ({ ...s, status: 'loading', error: null }));
    this.sub = source.subscribe({
      next: (value) => this.state.set({ status: 'ready', value, error: null }),
      error: (error: unknown) => {
        const stale = this.state().value !== null;
        this.state.update((s) => ({ ...s, status: 'error', error }));
        // With no value the view shows a designed error state; with one it would
        // look up to date, so say plainly that this is the previous result.
        if (stale) this.toasts.show(`Could not refresh: ${ApiError.from(error).userMessage}`, 'error');
      },
    });
  }

  set(value: T): void {
    this.state.set({ status: 'ready', value, error: null });
  }
}
