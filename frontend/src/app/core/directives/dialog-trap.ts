import { AfterViewInit, Directive, ElementRef, HostListener, OnDestroy, inject, input } from '@angular/core';

const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Keeps focus inside a modal dialog: focuses it on open, restores focus on close, traps Tab, and closes on Escape. */
@Directive({ selector: '[appDialogTrap]' })
export class DialogTrap implements AfterViewInit, OnDestroy {
  private readonly host = inject(ElementRef<HTMLElement>);
  appDialogTrap = input.required<() => void>();
  private previouslyFocused: HTMLElement | null = null;

  ngAfterViewInit() {
    this.previouslyFocused = document.activeElement as HTMLElement;
    (this.focusable()[0] ?? this.host.nativeElement).focus();
  }

  ngOnDestroy() {
    this.previouslyFocused?.focus?.();
  }

  @HostListener('keydown.escape')
  onEscape() {
    this.appDialogTrap()();
  }

  @HostListener('keydown.tab', ['$event'])
  onTab(event: Event) {
    const keyEvent = event as KeyboardEvent;
    const focusable = this.focusable();
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (keyEvent.shiftKey && document.activeElement === first) {
      keyEvent.preventDefault();
      last.focus();
    } else if (!keyEvent.shiftKey && document.activeElement === last) {
      keyEvent.preventDefault();
      first.focus();
    }
  }

  private focusable(): HTMLElement[] {
    return Array.from(this.host.nativeElement.querySelectorAll(FOCUSABLE)) as HTMLElement[];
  }
}
