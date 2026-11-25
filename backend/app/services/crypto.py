"""Fernet symmetric encryption helper for optional saved SSH credentials."""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    """Encrypts and decrypts secrets using a key persisted in the data volume."""

    def __init__(self, data_dir: Path) -> None:
        self.key_path = data_dir / ".secret.key"
        self._fernet: Fernet | None = None

    def _get_fernet(self) -> Fernet:
        if self._fernet is not None:
            return self._fernet

        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            self.key_path.write_bytes(key)
            try:
                os.chmod(self.key_path, 0o600)
            except OSError:
                pass
        self._fernet = Fernet(key)
        return self._fernet

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return ""
        token = self._get_fernet().encrypt(plaintext.encode("utf-8"))
        return token.decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        try:
            raw = self._get_fernet().decrypt(ciphertext.encode("ascii"))
            return raw.decode("utf-8")
        except (InvalidToken, ValueError):
            return ""
