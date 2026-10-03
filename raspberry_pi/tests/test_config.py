import pytest

from tests.conftest import TEST_CONTROLLER_URL

from app.config import ConfigError, load_settings


def test_valid_mock_env_loads(env):
    s = load_settings(env)
    assert s.mock_hardware is True
    assert s.esp32_controller_url is None
    assert s.responses_path.name == "en.json"


@pytest.mark.parametrize("missing", ["MOCK_HARDWARE", "API_PORT", "LOG_LEVEL", "CATALOG_DIR",
                                     "ESP32_HTTP_TIMEOUT_S", "ESP32_HTTP_RETRIES"])
def test_missing_required_value_fails_closed(env, missing):
    del env[missing]
    with pytest.raises(ConfigError, match=missing):
        load_settings(env)


def test_real_hardware_requires_controller_url(env):
    env["MOCK_HARDWARE"] = "false"
    with pytest.raises(ConfigError, match="ESP32_CONTROLLER_URL"):
        load_settings(env)


def test_real_hardware_rejects_placeholder_url(env):
    env["MOCK_HARDWARE"] = "false"
    env["ESP32_CONTROLLER_URL"] = "http://192.168.x.x"
    with pytest.raises(ConfigError, match="not a usable URL"):
        load_settings(env)


def test_real_hardware_url_is_normalised(env):
    env["MOCK_HARDWARE"] = "false"
    env["ESP32_CONTROLLER_URL"] = TEST_CONTROLLER_URL + "/"
    assert load_settings(env).esp32_controller_url == TEST_CONTROLLER_URL


@pytest.mark.parametrize("key,value", [("MOCK_HARDWARE", "maybe"), ("API_PORT", "abc"),
                                       ("ESP32_HTTP_TIMEOUT_S", "0"), ("LOG_LEVEL", "LOUD")])
def test_malformed_values_fail_closed(env, key, value):
    env[key] = value
    with pytest.raises(ConfigError, match=key):
        load_settings(env)
