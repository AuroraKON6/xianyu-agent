"""Geetest captcha configuration."""
import os


class GeetestConfig:
    """Geetest configuration class."""

    CAPTCHA_ID = os.getenv("GEETEST_CAPTCHA_ID", "a30cdbb466e9349385762477cb2c7df6")
    PRIVATE_KEY = os.getenv("GEETEST_PRIVATE_KEY", "6f70322308eb29ae0d85516a14a32d2c")
    USER_ID = os.getenv("GEETEST_USER_ID", "xianyu_system")
    CLIENT_TYPE = "web"
    API_URL = "http://api.geetest.com"
    REGISTER_URL = "/register.php"
    VALIDATE_URL = "/validate.php"
    TIMEOUT = 5
    VERSION = "python-fastapi:1.0.0"
