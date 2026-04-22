#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""Tuya Open API."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Dict, Optional, Tuple

import requests

from .openlogging import filter_logger, logger
from .version import VERSION

TUYA_ERROR_CODE_TOKEN_INVALID = 1010

TO_B_REFRESH_TOKEN_API = "/v1.0/token/{}"

TO_B_TOKEN_API = "/v1.0/token"


class TuyaTokenInfo:
    """Tuya token info.

    Attributes:
        access_token: Access token.
        expire_time: Valid period in seconds.
        refresh_token: Refresh token.
        uid: Tuya user ID.
    """

    def __init__(self, token_response: Dict[str, Any] = None):
        """Init TuyaTokenInfo."""
        result = token_response.get("result", {})

        self.expire_time = (
            token_response.get("t", 0)
            + result.get("expire", result.get("expire_time", 0)) * 1000
        )
        self.access_token = result.get("access_token", "")
        self.refresh_token = result.get("refresh_token", "")
        self.uid = result.get("uid", "")


class TuyaOpenAPI:
    """Open Api.

    Typical usage example:

    openapi = TuyaOpenAPI(ENDPOINT, ACCESS_ID, ACCESS_KEY)
    """

    def __init__(
        self,
        endpoint: str,
        access_id: str,
        access_secret: str,
        lang: str = "en",
        auth_scheme: str = "auto",
        app_identifier: str | None = None,
    ):
        """Init TuyaOpenAPI."""
        self.session = requests.session()

        self.endpoint = endpoint
        self.access_id = access_id
        self.access_secret = access_secret
        self.lang = lang
        self.auth_scheme = auth_scheme
        self.app_identifier = app_identifier or "com.sebastianprietoa.ambilight.localhost"
        self._resolved_auth_scheme: str | None = None
        self.last_connect_attempts: list[Dict[str, Any]] = []
        self.last_request_summary: Dict[str, Any] | None = None

        self.token_info: TuyaTokenInfo = None

        self.dev_channel: str = ""

    # https://developer.tuya.com/docs/iot/open-api/api-reference/singnature?id=Ka43a5mtx1gsc
    def _calculate_sign(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        auth_scheme: str = "cloud",
    ) -> Tuple[str, int, Dict[str, str]]:

        # HTTPMethod
        str_to_sign = method
        str_to_sign += "\n"

        # Content-SHA256
        content_to_sha256 = (
            "" if body is None or len(body.keys()) == 0 else json.dumps(body)
        )

        str_to_sign += (
            hashlib.sha256(content_to_sha256.encode(
                "utf8")).hexdigest().lower()
        )
        str_to_sign += "\n"

        signature_headers: Dict[str, str] = {}
        signature_headers_block = ""
        nonce = ""
        identifier = ""
        if auth_scheme == "app":
            nonce = uuid.uuid4().hex
            signature_headers = {
                "area_id": self.app_identifier,
                "req_id": uuid.uuid4().hex,
            }
            signature_headers_block = "".join(
                f"{key}:{value}\n" for key, value in signature_headers.items()
            )
            identifier = self.app_identifier

        # Header
        str_to_sign += signature_headers_block
        str_to_sign += "\n"

        # URL
        str_to_sign += path

        if params is not None and len(params.keys()) > 0:
            str_to_sign += "?"

            query_builder = ""
            params_keys = sorted(params.keys())

            for key in params_keys:
                query_builder += f"{key}={params[key]}&"
            str_to_sign += query_builder[:-1]

        # Sign
        t = int(time.time() * 1000)

        message = self.access_id
        if auth_scheme == "app":
            if self.token_info is not None and len(self.token_info.access_token) > 0:
                message += self.token_info.access_token
            message += str(t) + nonce + identifier + str_to_sign
        else:
            if self.token_info is not None:
                message += self.token_info.access_token
            message += str(t) + str_to_sign
        sign = (
            hmac.new(
                self.access_secret.encode("utf8"),
                msg=message.encode("utf8"),
                digestmod=hashlib.sha256,
            )
            .hexdigest()
            .upper()
        )
        request_headers: Dict[str, str] = {}
        if auth_scheme == "app":
            request_headers["nonce"] = nonce
            request_headers["Signature-Headers"] = "area_id:req_id"
            request_headers.update(signature_headers)
        return sign, t, request_headers

    @property
    def resolved_auth_scheme(self) -> str:
        return self._resolved_auth_scheme or ("cloud" if self.auth_scheme == "auto" else self.auth_scheme)

    @staticmethod
    def _sanitize_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not params:
            return {}
        sanitized: Dict[str, Any] = {}
        for key, value in params.items():
            sanitized[key] = "***" if key in {"code", "access_token", "refresh_token"} else value
        return sanitized

    @staticmethod
    def _response_summary(response: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if response is None:
            return {"success": False, "code": None, "msg": "No response", "tid": None}
        return {
            "success": bool(response.get("success")),
            "code": response.get("code"),
            "msg": response.get("msg"),
            "tid": response.get("tid"),
        }

    def __refresh_access_token_if_need(self, path: str):
        if self.is_connect() is False:
            return

        if path.startswith(TO_B_TOKEN_API):
            return

        # should use refresh token?
        now = int(time.time() * 1000)
        expired_time = self.token_info.expire_time

        if expired_time - 60 * 1000 > now:  # 1min
            return

        self.token_info.access_token = ""
        response = self.get(
            TO_B_REFRESH_TOKEN_API.format(self.token_info.refresh_token)
        )

        self.token_info = TuyaTokenInfo(response)

    def set_dev_channel(self, dev_channel: str):
        """Set dev channel."""
        self.dev_channel = dev_channel

    def connect(
        self
    ) -> Dict[str, Any]:
        """Connect to Tuya Cloud.

        Returns:
            response: connect response
        """
        self.last_connect_attempts = []
        schemes = [self.auth_scheme] if self.auth_scheme != "auto" else ["cloud", "app"]
        last_response: Dict[str, Any] | None = None
        for scheme in schemes:
            self.token_info = None
            self._resolved_auth_scheme = scheme
            response = self.get(TO_B_TOKEN_API, {"grant_type": 1})
            last_response = response
            self.last_connect_attempts.append(
                {"scheme": scheme, "response": self._response_summary(response)}
            )
            if response and response.get("success"):
                self.token_info = TuyaTokenInfo(response)
                return response

        self._resolved_auth_scheme = None
        return last_response or {"success": False, "msg": "Unable to connect to Tuya"}

    def connect_with_authorization_code(self, code: str) -> Dict[str, Any]:
        """Exchange an OAuth 2.0 authorization code for an access token."""
        previous_scheme = self._resolved_auth_scheme
        self.token_info = None
        self._resolved_auth_scheme = "app"
        response = self.get(TO_B_TOKEN_API, {"grant_type": 2, "code": code})
        self.last_connect_attempts = [
            {"scheme": "app-oauth-code", "response": self._response_summary(response)}
        ]
        if response and response.get("success"):
            self.token_info = TuyaTokenInfo(response)
            return response
        self._resolved_auth_scheme = previous_scheme
        return response

    def restore_token(self, token_response: Dict[str, Any]) -> None:
        """Restore an already-issued token response into the current client."""
        self.token_info = TuyaTokenInfo(token_response)
        if self._resolved_auth_scheme is None:
            self._resolved_auth_scheme = "app"

    def is_connect(self) -> bool:
        """Is connect to tuya cloud."""
        return self.token_info is not None and len(self.token_info.access_token) > 0

    def __request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:

        self.__refresh_access_token_if_need(path)

        access_token = ""
        if self.token_info:
            access_token = self.token_info.access_token

        sign, t, extra_headers = self._calculate_sign(
            method,
            path,
            params,
            body,
            auth_scheme=self.resolved_auth_scheme,
        )
        headers = {
            "client_id": self.access_id,
            "sign": sign,
            "sign_method": "HMAC-SHA256",
            "t": str(t),
            "lang": self.lang,
        }
        if access_token:
            headers["access_token"] = access_token
        headers.update(extra_headers)

        headers["dev_lang"] = "python"
        headers["dev_version"] = VERSION
        headers["dev_channel"] = f"cloud_{self.dev_channel}"

        logger.debug(
            f"Request: method = {method}, \
                url = {self.endpoint + path},\
                params = {params},\
                body = {filter_logger(body)},\
                t = {int(time.time()*1000)}"
        )

        response = self.session.request(
            method, self.endpoint + path, params=params, json=body, headers=headers
        )

        if response.ok is False:
            self.last_request_summary = {
                "method": method,
                "path": path,
                "params": self._sanitize_params(params),
                "auth_scheme": self.resolved_auth_scheme,
                "http_status": response.status_code,
                "success": False,
                "code": None,
                "msg": response.text,
                "tid": None,
            }
            logger.error(
                f"Response error: code={response.status_code}, body={response.text}"
            )
            return None

        result = response.json()
        self.last_request_summary = {
            "method": method,
            "path": path,
            "params": self._sanitize_params(params),
            "auth_scheme": self.resolved_auth_scheme,
            "http_status": response.status_code,
            "success": bool(result.get("success")),
            "code": result.get("code"),
            "msg": result.get("msg"),
            "tid": result.get("tid"),
        }

        logger.debug(
            f"Response: {json.dumps(filter_logger(result), ensure_ascii=False, indent=2)}"
        )

        if result.get("code", -1) == TUYA_ERROR_CODE_TOKEN_INVALID:
            self.token_info = None
            self.connect()

        return result

    def get(
        self, path: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Http Get.

        Requests the server to return specified resources.

        Args:
            path (str): api path
            params (map): request parameter

        Returns:
            response: response body
        """
        return self.__request("GET", path, params, None)

    def post(
        self, path: str, body: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Http Post.

        Requests the server to update specified resources.

        Args:
            path (str): api path
            body (map): request body

        Returns:
            response: response body
        """
        return self.__request("POST", path, None, body)

    def put(
        self, path: str, body: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Http Put.

        Requires the server to perform specified operations.

        Args:
            path (str): api path
            body (map): request body

        Returns:
            response: response body
        """
        return self.__request("PUT", path, None, body)

    def delete(
        self, path: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Http Delete.

        Requires the server to delete specified resources.

        Args:
            path (str): api path
            params (map): request param

        Returns:
            response: response body
        """
        return self.__request("DELETE", path, params, None)
