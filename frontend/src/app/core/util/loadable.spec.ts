import { Injector, runInInjectionContext } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Subject, throwError } from 'rxjs';
import { ToastService } from '../state/stores';
import { Loadable } from './loadable';

describe('Loadable', () => {
  let injector: Injector;
  let toasts: ToastService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    injector = TestBed.inject(Injector);
    toasts = TestBed.inject(ToastService);
  });

  const make = <T,>(): Loadable<T> => runInInjectionContext(injector, () => new Loadable<T>());

  it('exposes the first load as loading, then the value', () => {
    const source = new Subject<string>();
    const loadable = make<string>();
    loadable.load(source);
    expect(loadable.initialLoading()).toBe(true);
    source.next('first');
    expect(loadable.value()).toBe('first');
    expect(loadable.status()).toBe('ready');
    expect(loadable.initialLoading()).toBe(false);
  });

  it('shows no toast on a first failure, because the view renders an error state', () => {
    const loadable = make<string>();
    loadable.load(throwError(() => new Error('down')));
    expect(loadable.status()).toBe('error');
    expect(toasts.toasts().length).toBe(0);
  });

  it('announces a failed refresh instead of leaving stale data looking current', () => {
    const first = new Subject<string>();
    const loadable = make<string>();
    loadable.load(first);
    first.next('cached');
    loadable.load(throwError(() => new Error('offline')));
    expect(loadable.value()).toBe('cached');
    expect(toasts.toasts()[0].message).toContain('Could not refresh');
    expect(toasts.toasts()[0].tone).toBe('error');
  });

  it('drops the result of a superseded request', () => {
    const slow = new Subject<string>();
    const fast = new Subject<string>();
    const loadable = make<string>();
    loadable.load(slow);
    loadable.load(fast);
    fast.next('newest');
    slow.next('stale');
    expect(loadable.value()).toBe('newest');
  });
});
