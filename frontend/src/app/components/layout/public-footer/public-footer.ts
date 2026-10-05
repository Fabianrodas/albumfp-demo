import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { APP_VERSION, AUTOR, REDES } from '../../../core/site';

@Component({ selector: 'app-public-footer', imports: [RouterLink], templateUrl: './public-footer.html', styleUrl: './public-footer.css' })
export class PublicFooter {
  readonly version = APP_VERSION;
  readonly autor = AUTOR;
  readonly redes = REDES;
}
