import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { InfiniteScroll } from './infinite-scroll';

class ControlledObserver implements IntersectionObserver {
  static current?: ControlledObserver;
  readonly root = null;
  readonly rootMargin = '0px';
  readonly scrollMargin = '0px';
  readonly thresholds = [0];

  constructor(private readonly callback: IntersectionObserverCallback) {
    ControlledObserver.current = this;
  }

  disconnect() {}
  observe() {}
  takeRecords(): IntersectionObserverEntry[] { return []; }
  unobserve() {}
  show(isIntersecting: boolean) {
    this.callback([{ isIntersecting } as IntersectionObserverEntry], this);
  }
}

@Component({
  imports: [InfiniteScroll],
  template: '<div appInfiniteScroll [enabled]="enabled()" [busy]="busy()" (reached)="onReached()"></div>',
})
class InfiniteScrollHost {
  enabled = signal(true);
  busy = signal(false);
  loads = 0;
  onReached() { this.loads += 1; }
}

describe('InfiniteScroll', () => {
  let originalObserver: typeof IntersectionObserver | undefined;

  beforeEach(() => {
    originalObserver = globalThis.IntersectionObserver;
    globalThis.IntersectionObserver = ControlledObserver as unknown as typeof IntersectionObserver;
  });

  afterEach(() => {
    globalThis.IntersectionObserver = originalObserver as typeof IntersectionObserver;
    ControlledObserver.current = undefined;
  });

  it('emits once per completed page while the sentinel stays visible', async () => {
    const fixture = TestBed.createComponent(InfiniteScrollHost);
    fixture.detectChanges();

    ControlledObserver.current!.show(true);
    await fixture.whenStable();
    expect(fixture.componentInstance.loads).toBe(1);

    ControlledObserver.current!.show(true);
    await fixture.whenStable();
    expect(fixture.componentInstance.loads).toBe(1);

    fixture.componentInstance.busy.set(true);
    fixture.detectChanges();
    fixture.componentInstance.busy.set(false);
    fixture.detectChanges();
    await fixture.whenStable();
    expect(fixture.componentInstance.loads).toBe(2);
  });

  it('does not emit while disabled', async () => {
    const fixture = TestBed.createComponent(InfiniteScrollHost);
    fixture.componentInstance.enabled.set(false);
    fixture.detectChanges();

    ControlledObserver.current!.show(true);
    await fixture.whenStable();
    expect(fixture.componentInstance.loads).toBe(0);
  });
});
