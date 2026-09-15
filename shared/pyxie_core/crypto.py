import os

from cryptography.fernet import Fernet

_key = os.environ["PYXIE_CREDENTIAL_KEY"].encode()
_fernet = Fernet(_key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()


def mask_secret(plaintext: str, visible: int = 4) -> str:
    if len(plaintext) <= visible:
        return "*" * len(plaintext)
    return "*" * (len(plaintext) - visible) + plaintext[-visible:]
