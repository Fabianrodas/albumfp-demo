import { AfterViewInit, Directive, ElementRef, inject, input } from '@angular/core';
import { onceVisible } from '../utils/visibility';

/** Fades an element (or, in 'stagger' mode, its direct children) up into view once it scrolls near the viewport. */
@Directive({ selector: '[appScrollReveal]' })
export class ScrollReveal implements AfterViewInit {
  private readonly host = inject(ElementRef<HTMLElement>);
  appScrollReveal = input<'item' | 'stagger' | ''>('item');

  ngAfterViewInit() {
    const el = this.host.nativeElement;
    el.setAttribute(this.appScrollReveal() === 'stagger' ? 'data-reveal-stagger' : 'data-reveal', '');
    if (typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches) {
      el.classList.add('is-visible');
      return;
    }
    onceVisible(this.host, () => el.classList.add('is-visible'), '0px 0px -60px 0px');
  }
}
