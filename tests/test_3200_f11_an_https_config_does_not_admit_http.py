"""3.20, F11 (Claude Security scan, LOW): a config entry that is a URL admits its origin, not every scheme on its host.

THE DEFECT. `_http.host_allowed` compared hosts only. With `embed.url = https://api.example-embeddings.com/...` it
returned True for `http://api.example-embeddings.com/...`, and `env_key()` then sent the API key in an environment
variable over plain http to the host the user had configured over https. Measured on v3.19.1 (AUDIT-A low_repro.py).

WHAT IS PINNED HERE. A host named by an https URL, in `embed.url` or `embed.allowed_hosts`, is admitted only over
https. The port is still not compared: the 3.16.4 rule, the owner's decision, is that the user's config names a HOST
(test_3164_f12_an_env_url_needs_the_users_config.py pins a different port on the same host). An http entry and a
bare host entry admit the host on any scheme, which is what a user writes for a machine on their own network served
over http. Loopback stays allowed.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import _http  # noqa: E402

HTTPS = {"url": "https://api.example-embeddings.com/v1/embeddings"}


def test_an_https_config_does_not_admit_http_on_the_same_host():
    assert _http.host_allowed("http://api.example-embeddings.com/v1/embeddings", HTTPS) is False


def test_an_https_config_still_admits_https_on_its_host_on_any_port():
    assert _http.host_allowed("https://api.example-embeddings.com/v1/other", HTTPS) is True
    assert _http.host_allowed("https://api.example-embeddings.com:8443/v1/embeddings", HTTPS) is True


def test_an_http_config_admits_both_schemes():
    cfg = {"url": "http://192.168.0.126:11434/api/embed"}
    assert _http.host_allowed("http://192.168.0.126:11434/api/embed", cfg) is True
    assert _http.host_allowed("https://192.168.0.126/api/embed", cfg) is True


def test_an_https_url_in_allowed_hosts_admits_only_https_too():
    cfg = {"allowed_hosts": ["https://embed.example"]}
    assert _http.host_allowed("https://embed.example/x", cfg) is True
    assert _http.host_allowed("http://embed.example/x", cfg) is False


def test_a_bare_host_entry_admits_the_host_on_any_scheme_and_port():
    cfg = {"allowed_hosts": ["192.168.0.126"]}
    assert _http.host_allowed("http://192.168.0.126:11434/api/embed", cfg) is True
    assert _http.host_allowed("https://192.168.0.126/api/embed", cfg) is True


def test_another_host_and_loopback_are_unchanged():
    assert _http.host_allowed("http://evil.example/v1", HTTPS) is False
    assert _http.host_allowed("http://127.0.0.1:11434/api/embed", HTTPS) is True


def test_env_key_withholds_the_key_from_the_http_downgrade(monkeypatch, capsys):
    monkeypatch.setenv("INSPEXIMUS_EMBED_KEY", "sk-not-a-real-key")
    assert _http.env_key("INSPEXIMUS_EMBED_KEY", "http://api.example-embeddings.com/v1/embeddings", HTTPS) == ""
    assert "the key is not sent" in capsys.readouterr().err
    assert _http.env_key("INSPEXIMUS_EMBED_KEY", "https://api.example-embeddings.com/v1/embeddings", HTTPS) \
        == "sk-not-a-real-key", "CONTROL: the configured origin gets the key"
