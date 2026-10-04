from passlib.hash import argon2

def hash_password(password: str) -> str:
    return argon2.hash(password)

def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    return argon2.verify(password, password_hash)