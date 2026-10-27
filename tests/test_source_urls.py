import pytest

from cloverboard.server.sources import load_sources


@pytest.mark.parametrize("address, url, token", [
    ("10.0.0.12:7230?token=secret", "http://10.0.0.12:7230", "secret"),
    ("http://10.0.0.12:7230/?token=secret", "http://10.0.0.12:7230", "secret"),
    ("gpu=https://peer.test/prefix/?token=a%3D%26%2B%2F", "https://peer.test/prefix", "a=&+/"),
    ("gpu=[fd00::7]:7230?token=secret", "http://[fd00::7]:7230", "secret"),
    ("gpu=10.0.0.12:7230", "http://10.0.0.12:7230", None),
])
def test_peer_addresses_extract_token_and_normalize_url(address, url, token):
    source, = load_sources(address)
    assert source.url == url
    assert source.token == token
    assert source.key == ("gpu" if address.startswith("gpu=") else load_sources(url)[0].key)


def test_peer_identity_does_not_include_token():
    first, = load_sources("10.0.0.12:7230?token=first")
    second, = load_sources("http://10.0.0.12:7230/?token=second")
    assert first.key == second.key
    assert first.label == second.label
    assert first.key.startswith("peer-")


@pytest.mark.parametrize("address", [
    "10.0.0.12:7230?token=",
    "10.0.0.12:7230?token=secret&token=other",
    "10.0.0.12:7230?other=secret",
    "10.0.0.12:7230?token=secret&other=value",
    "10.0.0.12:7230?token=secret%0A",
    "http://user:secret@peer.test",
    "http://peer.test:invalid?token=secret",
    "ftp://peer.test?token=secret",
])
def test_invalid_peer_urls_fail_without_echoing_credentials(address):
    with pytest.raises(ValueError) as error:
        load_sources(address)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("setting", ['token="other"', 'token_env="PEER_TOKEN"'])
def test_source_config_rejects_conflicting_token_settings(tmp_path, setting):
    config = tmp_path / "sources.toml"
    config.write_text(f'[sources.gpu]\nurl="http://peer.test?token=secret"\n{setting}\n')
    with pytest.raises(ValueError, match="Specify the token only once"):
        load_sources(config=config)


def test_source_config_accepts_share_url(tmp_path):
    config = tmp_path / "sources.toml"
    config.write_text('[sources.gpu]\nurl="http://peer.test/?token=secret"\n')
    source, = load_sources(config=config)
    assert (source.key, source.url, source.token) == ("gpu", "http://peer.test", "secret")
