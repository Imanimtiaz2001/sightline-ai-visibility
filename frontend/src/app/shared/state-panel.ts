import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { ApiError } from '../core/api/api-error';

/**
 * The one component for loading, empty and error states, so no screen is ever
 * blank and no raw error text reaches the user. Errors show what happened, what
 * to do, and the request reference to quote to support.
 */
@Component({
  selector: 'app-state',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @switch (kind()) {
      @case ('loading') {
        <div class="state loading" role="status" aria-live="polite">
          @for (row of rows(); track $index) {
            <div class="skeleton bar" [style.width.%]="row"></div>
          }
          <span class="sr-only">{{ title() || 'Loading' }}</span>
        </div>
      }
      @case ('error') {
        <div class="state message error" role="alert">
          <h3>{{ title() || 'This could not be loaded' }}</h3>
          <p class="muted">{{ errorMessage() }}</p>
          @if (reference()) {
            <p class="faint small">Reference: <span class="num">{{ reference() }}</span></p>
          }
          <div class="row actions">
            @if (retryable()) {
              <button type="button" class="btn" (click)="retry.emit()">Try again</button>
            }
            <ng-content select="[actions]" />
          </div>
        </div>
      }
      @default {
        <div class="state message">
          <h3>{{ title() }}</h3>
          @if (message()) {
            <p class="muted">{{ message() }}</p>
          }
          <div class="row actions"><ng-content select="[actions]" /></div>
        </div>
      }
    }
  `,
  styles: `
    .state { padding: var(--s-6) var(--s-5); }
    .loading { display: grid; gap: var(--s-3); }
    .bar { height: 14px; }
    .message { display: grid; gap: var(--s-2); justify-items: start; border: 1px dashed var(--line-strong);
      border-radius: var(--r-lg); background: var(--hatch), var(--surface); }
    .message.error { border-style: solid; border-color: var(--danger-soft); background: var(--surface); }
    .error h3 { color: var(--danger); }
    .actions { margin-top: var(--s-3); }
    .actions:empty { display: none; }
  `,
})
export class StatePanel {
  readonly kind = input<'loading' | 'empty' | 'error'>('loading');
  readonly title = input('');
  readonly message = input('');
  readonly error = input<unknown>(null);
  readonly lines = input(3);
  readonly retry = output<void>();

  protected readonly rows = computed(() => Array.from({ length: this.lines() }, (_, i) => 100 - ((i * 17) % 45)));
  protected readonly apiError = computed(() => (this.error() ? ApiError.from(this.error()) : null));
  protected readonly errorMessage = computed(() => this.apiError()?.userMessage ?? this.message());
  protected readonly reference = computed(() => this.apiError()?.requestId ?? null);
  protected readonly retryable = computed(() => this.apiError()?.isRetryable ?? true);
}
