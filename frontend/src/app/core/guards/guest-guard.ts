import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { map } from 'rxjs';
import { Auth } from '../services/auth';

export const guestGuard: CanActivateFn = () => {
  const auth = inject(Auth);
  const router = inject(Router);
  return auth.ensureSession().pipe(map(authenticated => authenticated ? router.createUrlTree(['/inicio']) : true));
};
