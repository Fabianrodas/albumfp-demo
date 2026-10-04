import { Component, inject, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AlbumApi, AlbumOwner } from '../../../core/services/album-api';

/**
 * Autor clicable: foto y usuario, enlazado a su perfil público. Se usa en el
 * feed del inicio, en el directorio y en la cabecera del perfil.
 */
@Component({
  selector: 'app-user-chip',
  imports: [RouterLink],
  templateUrl: './user-chip.html',
  styleUrl: './user-chip.css',
})
export class UserChip {
  private readonly api = inject(AlbumApi);

  user = input.required<AlbumOwner>();
  size = input<'sm' | 'md'>('sm');
  /** Muestra el nombre completo debajo del usuario. */
  showFullName = input(false);

  avatarUrl() { return this.api.avatarUrl(this.user().id); }
  initials() { return this.user().username.slice(0, 2).toUpperCase(); }
}
