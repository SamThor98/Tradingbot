"""Local dashboard HTTPS cert must be reused so open tabs keep fetching."""

from __future__ import annotations

import ipaddress

from cryptography import x509
from cryptography.hazmat.backends import default_backend


def test_make_cert_reuses_existing_pair(tmp_path) -> None:
    from run_dual_auth_browser import _make_cert

    first_cert, first_key = _make_cert(skill_dir=tmp_path)
    original = first_cert.read_bytes()
    second_cert, second_key = _make_cert(skill_dir=tmp_path)
    assert first_cert == second_cert
    assert first_key == second_key
    assert second_cert.read_bytes() == original


def test_new_localhost_cert_includes_ip_san(tmp_path) -> None:
    from run_dual_auth_browser import _make_cert

    cert_path, _key_path = _make_cert(skill_dir=tmp_path)
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes(), default_backend())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert ipaddress.IPv4Address("127.0.0.1") in san.get_values_for_type(x509.IPAddress)
    assert "localhost" in san.get_values_for_type(x509.DNSName)
