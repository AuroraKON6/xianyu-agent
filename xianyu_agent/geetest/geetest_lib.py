"""Geetest captcha SDK core library.

Supports:
- Captcha initialization (register) - get challenge parameters
- Secondary validation (validate) - verify user slider result
- MD5/SHA256/HMAC-SHA256 encryption
- Fallback mode when API is down
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional

from loguru import logger

try:
    import httpx
except ImportError:
    httpx = None

from .geetest_config import GeetestConfig


class DigestMod(Enum):
    MD5 = "md5"
    SHA256 = "sha256"
    HMAC_SHA256 = "hmac-sha256"


@dataclass
class GeetestResult:
    """Geetest result wrapper."""
    status: int = 0  # 1=success, 0=failure
    data: str = ""
    msg: str = ""

    def to_dict(self) -> dict:
        if self.data:
            try:
                return json.loads(self.data)
            except json.JSONDecodeError:
                return {}
        return {}


class GeetestLib:
    """Geetest captcha SDK core class."""

    def __init__(self, captcha_id: Optional[str] = None, private_key: Optional[str] = None):
        self.captcha_id = captcha_id or GeetestConfig.CAPTCHA_ID
        self.private_key = private_key or GeetestConfig.PRIVATE_KEY
        self.result = GeetestResult()

    def _md5_encode(self, value: str) -> str:
        return hashlib.md5(value.encode("utf-8")).hexdigest()

    def _sha256_encode(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _hmac_sha256_encode(self, value: str, key: str) -> str:
        return hmac.new(key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()

    def _encrypt_challenge(self, origin_challenge: str, digest_mod: DigestMod) -> str:
        if digest_mod == DigestMod.MD5:
            return self._md5_encode(origin_challenge + self.private_key)
        elif digest_mod == DigestMod.SHA256:
            return self._sha256_encode(origin_challenge + self.private_key)
        elif digest_mod == DigestMod.HMAC_SHA256:
            return self._hmac_sha256_encode(origin_challenge, self.private_key)
        return self._md5_encode(origin_challenge + self.private_key)

    async def _request_register(self, params: Dict[str, str]) -> str:
        if httpx is None:
            logger.error("httpx not installed")
            return ""
        params.update({"gt": self.captcha_id, "json_format": "1", "sdk": GeetestConfig.VERSION})
        url = f"{GeetestConfig.API_URL}{GeetestConfig.REGISTER_URL}"
        try:
            async with httpx.AsyncClient(timeout=GeetestConfig.TIMEOUT) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                return data.get("challenge", "")
        except Exception as e:
            logger.error(f"Geetest register request failed: {e}")
            return ""

    def _build_register_result(self, origin_challenge: Optional[str], digest_mod: Optional[DigestMod]):
        if not origin_challenge or origin_challenge == "0":
            challenge = uuid.uuid4().hex
            data = {"success": 0, "gt": self.captcha_id, "challenge": challenge, "new_captcha": True}
            self.result = GeetestResult(status=0, data=json.dumps(data), msg="Register failed, using fallback mode")
        else:
            challenge = self._encrypt_challenge(origin_challenge, digest_mod or DigestMod.MD5)
            data = {"success": 1, "gt": self.captcha_id, "challenge": challenge, "new_captcha": True}
            self.result = GeetestResult(status=1, data=json.dumps(data), msg="")

    async def register(
        self,
        digest_mod: DigestMod = DigestMod.MD5,
        user_id: Optional[str] = None,
        client_type: Optional[str] = None,
    ) -> GeetestResult:
        """Initialize captcha verification."""
        logger.info(f"Geetest register: digest_mod={digest_mod.value}")
        params = {
            "digestmod": digest_mod.value,
            "user_id": user_id or GeetestConfig.USER_ID,
            "client_type": client_type or GeetestConfig.CLIENT_TYPE,
        }
        origin_challenge = await self._request_register(params)
        self._build_register_result(origin_challenge, digest_mod)
        logger.info(f"Geetest register complete: status={self.result.status}")
        return self.result

    def local_init(self) -> GeetestResult:
        """Local initialization (fallback mode)."""
        logger.info("Geetest local init (fallback mode)")
        self._build_register_result(None, None)
        return self.result

    async def _request_validate(self, challenge: str, validate: str, seccode: str, params: Dict[str, str]) -> str:
        if httpx is None:
            return ""
        params.update({
            "seccode": seccode,
            "json_format": "1",
            "challenge": challenge,
            "sdk": GeetestConfig.VERSION,
            "captchaid": self.captcha_id,
        })
        url = f"{GeetestConfig.API_URL}{GeetestConfig.VALIDATE_URL}"
        try:
            async with httpx.AsyncClient(timeout=GeetestConfig.TIMEOUT) as client:
                response = await client.post(url, data=params)
                response.raise_for_status()
                data = response.json()
                return data.get("seccode", "")
        except Exception as e:
            logger.error(f"Geetest validate request failed: {e}")
            return ""

    def _check_params(self, challenge: str, validate: str, seccode: str) -> bool:
        return bool(challenge and challenge.strip() and validate and validate.strip() and seccode and seccode.strip())

    async def success_validate(
        self,
        challenge: str,
        validate: str,
        seccode: str,
        user_id: Optional[str] = None,
        client_type: Optional[str] = None,
    ) -> GeetestResult:
        """Validate in normal mode."""
        logger.info(f"Geetest validate (normal): challenge={challenge[:16]}...")
        if not self._check_params(challenge, validate, seccode):
            return GeetestResult(status=0, data="", msg="Parameters cannot be empty")
        params = {"user_id": user_id or GeetestConfig.USER_ID, "client_type": client_type or GeetestConfig.CLIENT_TYPE}
        response_seccode = await self._request_validate(challenge, validate, seccode, params)
        if not response_seccode:
            return GeetestResult(status=0, data="", msg="Validate request failed")
        if response_seccode == "false":
            return GeetestResult(status=0, data="", msg="Validation failed")
        return GeetestResult(status=1, data="", msg="")

    def fail_validate(self, challenge: str, validate: str, seccode: str) -> GeetestResult:
        """Validate in fallback mode (simple parameter check)."""
        logger.info(f"Geetest validate (fallback): challenge={challenge[:16] if challenge else 'None'}...")
        if not self._check_params(challenge, validate, seccode):
            return GeetestResult(status=0, data="", msg="Fallback mode: parameters cannot be empty")
        return GeetestResult(status=1, data="", msg="")
