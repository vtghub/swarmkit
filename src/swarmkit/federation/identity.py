"""ed25519 identity for a swarmkitd daemon, and an explicit peer registry.

Federation here is deliberately minimal: no auto-discovery, no
trust-on-first-use. A daemon generates its own keypair on first run; two
daemons become peers only when an operator runs `swarmkit peer add` on each
side with the other's public key, exchanged out-of-band. This is honest
about what it doesn't solve (certificate provisioning, revocation, transport
encryption beyond what the signature gives you) in exchange for something
real and verifiable: a request claiming to be from peer X either carries a
valid signature from X's registered key, or it's rejected.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


@dataclass
class Identity:
    private_key: Ed25519PrivateKey

    @property
    def public_key_hex(self) -> str:
        raw = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return raw.hex()

    def sign(self, message: bytes) -> str:
        return self.private_key.sign(message).hex()


def load_or_create_identity(key_path: str | Path) -> Identity:
    """Load the ed25519 private key at `key_path`, or generate and persist a
    new one (mode 0600) if it doesn't exist yet. Same identity survives a
    daemon restart because it's read from this same file every time."""
    path = Path(key_path)
    if path.exists():
        raw = bytes.fromhex(path.read_text().strip())
        return Identity(Ed25519PrivateKey.from_private_bytes(raw))

    private_key = Ed25519PrivateKey.generate()
    raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw.hex())
    path.chmod(0o600)
    return Identity(private_key)


def verify(public_key_hex: str, message: bytes, signature_hex: str) -> bool:
    """Return True iff `signature_hex` is a valid ed25519 signature of
    `message` under `public_key_hex`. Never raises — any malformed input
    (bad hex, wrong length, tampered signature) is just a failed verification."""
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public_key.verify(bytes.fromhex(signature_hex), message)
        return True
    except Exception:
        return False


@dataclass
class Peer:
    name: str
    host: str
    port: int
    public_key_hex: str


class PeerRegistry:
    """Explicit, operator-managed peer list, persisted as JSON. No
    auto-discovery: a peer exists here only because `add()` was called with
    a public key the operator obtained out-of-band."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._peers: dict[str, Peer] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text())
        self._peers = {name: Peer(**data) for name, data in raw.items()}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({name: asdict(p) for name, p in self._peers.items()}, indent=2))

    def add(self, name: str, host: str, port: int, public_key_hex: str) -> None:
        self._peers[name] = Peer(name=name, host=host, port=port, public_key_hex=public_key_hex)
        self._save()

    def remove(self, name: str) -> bool:
        if name in self._peers:
            del self._peers[name]
            self._save()
            return True
        return False

    def get(self, name: str) -> Peer | None:
        return self._peers.get(name)

    def list(self) -> list[Peer]:
        return list(self._peers.values())
