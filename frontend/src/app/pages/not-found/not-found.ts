import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Theme } from '../../core/services/theme';
@Component({ selector: 'app-not-found', imports: [RouterLink], templateUrl: './not-found.html', styleUrl: './not-found.css' })
export class NotFound {
  readonly theme = inject(Theme);
}
