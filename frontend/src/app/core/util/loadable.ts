import { DestroyRef, Signal, computed, inject, signal } from '@angular/core';
import { Observable, Subscription } from 'rxjs';

export type LoadStatus = 'idle' | 'loading' | 'ready' | 'error';

/**
 * Async value as signals: `value`, `status`, `error`. `load()` cancels the
 * previous request (latest wins) and the subscription dies with the component.
 * Keeping the last value while reloading avoids flashing skeletons on refresh.
 * Must be created in an injection context.
 */
export class Loadable<T> {
  private readonly state = signal<{ status: LoadStatus; value: T | null; error: unknown }>({
    status: 'idle',
    value: null,
    error: null,
  });
  private sub: Subscription | null = null;

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
      error: (error: unknown) => this.state.update((s) => ({ ...s, status: 'error', error })),
    });
  }

  set(value: T): void {
    this.state.set({ status: 'ready', value, error: null });
  }
}
