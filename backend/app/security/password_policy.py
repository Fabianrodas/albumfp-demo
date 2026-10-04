"""Politica de contrasenas.

Hay dos funciones y la diferencia importa:

- `validate_password` es la regla **local y pura**. No toca la red. Es la que
  replica el frontend en `core/utils/auth-validation.ts`, asi que si empezara a
  hacer llamadas esa copia dejaria de ser equivalente.
- `validate_new_password` es la que usan el registro y el cambio de contrasena:
  aplica la local y, solo si esa pasa, consulta Pwned Passwords.

El login no usa ninguna de las dos: comprobar contra filtraciones una contrasena
que ya existe no protege nada y meteria una llamada de red en cada inicio de
sesion.
"""
from ..integrations.pwned_passwords import is_password_pwned

PWNED_MESSAGE = "Esta contraseña apareció en filtraciones conocidas. Elige otra."

# NIST SP 800-63B (S02 del roadmap): sin reglas de composicion -- nada de
# mayuscula/simbolo obligatorio -- y un minimo mas alto en su lugar. 15 es el
# umbral que NIST considera aceptable para autenticacion de un solo factor
# (usuario + contraseña, sin 2FA todavia).
MIN_PASSWORD_LENGTH = 15
# Techo defensivo, no una regla de fortaleza: Argon2 procesa la entrada
# entera, asi que una contraseña de megabytes seria una forma barata de
# gastar CPU del servidor. 128 es el mismo limite que el frontend ya
# anunciaba (`MAX_PASSWORD` en `auth-validation.ts`) sin que el backend lo
# hiciera cumplir -- una peticion fabricada a mano podia saltarselo.
MAX_PASSWORD_LENGTH = 128


def validate_password(pw: str):
    # Un cuerpo JSON puede traer CUALQUIER tipo: `{"password": 1234}` es un int
    # bien formado, y `1234 or ""` lo deja pasar por ser truthy. Sin esta guarda
    # el `len()` de abajo lanzaba TypeError y el endpoint devolvia 500 en vez de
    # 400 -- un 500 le dice a quien prueba que encontro algo que nadie previo.
    # Va aqui y no en cada endpoint porque aqui pasan todos los llamadores.
    if pw is not None and not isinstance(pw, str):
        return False, "La contraseña debe ser texto"
    pw = pw or ""
    if len(pw) < MIN_PASSWORD_LENGTH:
        return False, f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres"
    if len(pw) > MAX_PASSWORD_LENGTH:
        return False, f"La contraseña no puede superar {MAX_PASSWORD_LENGTH} caracteres"
    if pw.isdigit():
        return False, "La contraseña no puede ser solo números"
    return True, None


def validate_new_password(pw: str):
    valid, error = validate_password(pw)
    if not valid:
        return False, error

    # `is_password_pwned` devuelve None cuando HIBP no responde. Eso NO bloquea:
    # la regla local ya paso y una caida de un tercero no puede dejar a nadie
    # sin poder registrarse ni cambiar su contrasena.
    if is_password_pwned(pw) is True:
        return False, PWNED_MESSAGE
    return True, None
