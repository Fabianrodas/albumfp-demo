import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { Auth } from '../../../core/services/auth';
import { Toast } from '../../../core/services/toast';
import { errorInterceptor } from '../../../core/interceptors/error-interceptor';
import { Profile } from './profile';

/** F06 (Macro A finding 3): una contraseña actual equivocada es un 400 del
 * servidor. Pasa por el interceptor REAL y no debe tocar la sesión. */
describe('Profile password change with a wrong current password', () => {
  it('shows the server message and keeps the session', () => {
    TestBed.configureTestingModule({
      imports: [Profile],
      providers: [provideHttpClient(withInterceptors([errorInterceptor])), provideHttpClientTesting(), provideRouter([])],
    });
    const http = TestBed.inject(HttpTestingController);
    const auth = TestBed.inject(Auth);
    auth.user.set({ id: 9, username: 'ana', full_name: 'Ana', has_avatar: false });
    const clear = vi.spyOn(auth, 'clearLocalState');
    const toastError = vi.spyOn(TestBed.inject(Toast), 'error');
    const fixture = TestBed.createComponent(Profile);
    fixture.detectChanges();
    http.match(() => true).forEach(r => r.flush({ data: r.request.url.endsWith('preferences') ? {} : [], message: 'ok' }));

    const profile = fixture.componentInstance;
    profile.currentPassword = 'equivocada';
    profile.newPassword = profile.repeatPassword = 'una frase nueva y larga';
    profile.savePassword();
    http.expectOne('/auth/me/password').flush(
      { ok: false, message: 'La contraseña actual no es correcta', code: 'invalid_current_password' },
      { status: 400, statusText: 'Bad Request' });

    expect(toastError).toHaveBeenCalledWith('La contraseña actual no es correcta');
    expect(clear, 'a 400 never ends the session').not.toHaveBeenCalled();
  });
});
