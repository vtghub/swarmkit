"""Proves ed25519 identities survive a simulated daemon restart (same
keypair reloaded from the same key file), that verify() genuinely checks
the signature (not just presence of one), and that PeerRegistry persists
across reload with no auto-discovery — an entry exists only because add()
was called explicitly."""

from __future__ import annotations

from swarmkit.federation.identity import PeerRegistry, load_or_create_identity, verify


def test_identity_persists_across_reload(tmp_path):
    key_path = tmp_path / "identity.key"
    first = load_or_create_identity(key_path)
    second = load_or_create_identity(key_path)
    assert first.public_key_hex == second.public_key_hex


def test_identity_key_file_is_mode_0600(tmp_path):
    key_path = tmp_path / "identity.key"
    load_or_create_identity(key_path)
    mode = key_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_sign_and_verify_round_trip(tmp_path):
    identity = load_or_create_identity(tmp_path / "identity.key")
    message = b"a real message that should be signed"
    signature = identity.sign(message)
    assert verify(identity.public_key_hex, message, signature)


def test_verify_rejects_tampered_message(tmp_path):
    identity = load_or_create_identity(tmp_path / "identity.key")
    message = b"original message"
    signature = identity.sign(message)
    assert not verify(identity.public_key_hex, b"tampered message", signature)


def test_verify_rejects_tampered_signature(tmp_path):
    identity = load_or_create_identity(tmp_path / "identity.key")
    message = b"a message"
    signature = identity.sign(message)
    tampered = ("11" if signature[-2:] != "11" else "22") + signature[2:]
    assert not verify(identity.public_key_hex, message, tampered)


def test_verify_rejects_wrong_key(tmp_path):
    id_a = load_or_create_identity(tmp_path / "a.key")
    id_b = load_or_create_identity(tmp_path / "b.key")
    message = b"a message"
    signature = id_a.sign(message)
    assert not verify(id_b.public_key_hex, message, signature)


def test_verify_never_raises_on_malformed_input():
    assert verify("not-hex-!!", b"msg", "also-not-hex") is False
    assert verify("", b"msg", "") is False


def test_peer_registry_add_list_get_remove(tmp_path):
    registry = PeerRegistry(tmp_path / "peers.json")
    assert registry.list() == []
    assert registry.get("bob") is None

    registry.add("bob", "127.0.0.1", 9100, "ab" * 32)
    peers = registry.list()
    assert len(peers) == 1
    assert registry.get("bob").host == "127.0.0.1"
    assert registry.get("bob").port == 9100

    assert registry.remove("bob") is True
    assert registry.get("bob") is None
    assert registry.remove("bob") is False


def test_peer_registry_persists_across_reload(tmp_path):
    path = tmp_path / "peers.json"
    first = PeerRegistry(path)
    first.add("carol", "10.0.0.5", 9200, "cd" * 32)

    second = PeerRegistry(path)
    peer = second.get("carol")
    assert peer is not None
    assert peer.host == "10.0.0.5"
    assert peer.port == 9200


def test_peer_registry_has_no_auto_discovery(tmp_path):
    """A peer that was never explicitly added simply doesn't exist — there's
    no mechanism here that could populate it."""
    registry = PeerRegistry(tmp_path / "peers.json")
    assert registry.get("anyone-not-added") is None
