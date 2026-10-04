import { inject } from '@angular/core';
import { CanActivateChildFn, CanActivateFn, Router } from '@angular/router';
import { map } from 'rxjs';
import { Auth } from '../services/auth';

function checkAuthenticated(stateUrl: string) {
  const auth = inject(Auth);
  const router = inject(Router);
  return auth.ensureSession().pipe(
    map(authenticated => authenticated
      ? true
      : router.createUrlTree(['/login'], { queryParams: { returnUrl: stateUrl } }))
  );
}

export const authGuard: CanActivateFn = (_route, state) => checkAuthenticated(state.url);
export const authChildGuard: CanActivateChildFn = (_route, state) => checkAuthenticated(state.url);
