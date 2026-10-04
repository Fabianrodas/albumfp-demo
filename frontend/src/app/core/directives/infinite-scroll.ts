import { AfterViewInit, Directive, ElementRef, OnDestroy, effect, inject, input, output, signal } from '@angular/core';

/** Emits once per completed page while the sentinel remains near the viewport. */
@Directive({ selector: '[appInfiniteScroll]' })
export class InfiniteScroll implements AfterViewInit, OnDestroy {
  private readonly host = inject(ElementRef<HTMLElement>);
  private readonly visible = signal(false);
  private observer?: IntersectionObserver;
  private latched = false;

  enabled = input(true);
  busy = input(false);
  reached = output<void>();

  constructor() {
    effect(() => {
      if (this.busy()) {
        this.latched = false;
        return;
      }
      if (!this.enabled() || !this.visible() || this.latched) return;
      this.latched = true;
      queueMicrotask(() => {
        if (this.enabled() && !this.busy() && this.visible()) this.reached.emit();
      });
    });
  }

  ngAfterViewInit() {
    if (typeof IntersectionObserver === 'undefined') return;
    this.observer = new IntersectionObserver(entries => {
      const isVisible = entries.some(entry => entry.isIntersecting);
      if (!isVisible) this.latched = false;
      this.visible.set(isVisible);
    }, { rootMargin: '480px 0px' });
    this.observer.observe(this.host.nativeElement);
  }

  ngOnDestroy() { this.observer?.disconnect(); }
}
