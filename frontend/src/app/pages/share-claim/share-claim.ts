import { Component, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Auth } from '../../core/services/auth';
import { Theme } from '../../core/services/theme';
import { AlbumApi } from '../../core/services/album-api';

@Component({
  selector: 'app-share-claim',
  imports: [RouterLink],
  templateUrl: './share-claim.html',
  styleUrl: './share-claim.css',
})
export class ShareClaim {
  readonly theme = inject(Theme);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly auth = inject(Auth);
  private readonly api = inject(AlbumApi);

  token = this.route.snapshot.paramMap.get('token') || '';
  state = signal<'loading' | 'error'>('loading');
  error = signal('');

  ngOnInit() {
    if (!this.token) {
      this.fail('La invitación no es válida.');
      return;
    }

    this.auth.ensureSession().subscribe(authenticated => {
      if (!authenticated) {
        this.router.navigate(['/login'], {
          queryParams: { returnUrl: `/invitacion/${encodeURIComponent(this.token)}` },
          replaceUrl: true,
        });
        return;
      }
      this.claim();
    });
  }

  private claim() {
    this.api.claimShare(this.token).subscribe({
      next: response => this.router.navigate(['/albumes', response.data.album_id], { replaceUrl: true }),
      error: error => this.fail(error?.error?.message || 'No se pudo guardar este álbum en tu cuenta.'),
    });
  }

  private fail(message: string) {
    this.state.set('error');
    this.error.set(message);
  }
}
