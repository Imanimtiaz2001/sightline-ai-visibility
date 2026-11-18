import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { StatePanel } from '../shared/state-panel';

@Component({
  selector: 'app-not-found',
  imports: [RouterLink, StatePanel],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="page">
      <app-state kind="empty" title="This page does not exist" message="The link may be out of date, or the brand was deleted.">
        <a actions class="btn primary" routerLink="/">Go to the portfolio</a>
      </app-state>
    </div>
  `,
})
export class NotFoundPage {}
