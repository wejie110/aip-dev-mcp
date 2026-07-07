"""
Nacos 配置中心客户端（只读）。

从 server.py 提取，模块级变量 _nacos_token 改为实例属性，
NACOS 全局配置改为构造函数参数传入。
"""

import json
import os  # noqa: F401 — 预留
import yaml  # noqa: F401 — 预留
from http.client import HTTPConnection
from urllib.parse import urlencode, urlparse


class NacosClient:
    """
    Nacos 2.x HTTP API 客户端，自动处理登录认证与 token 过期重试。

    用法:
        client = NacosClient({
            "url": "http://nacos-server:8848",
            "username": "nacos",
            "password": "nacos",
            "namespace": "",          # 可选
        })
        result = client.query("/nacos/v1/cs/configs", {
            "dataId": "aip-nsos.yml",
            "group": "DEFAULT_GROUP",
        })
    """

    def __init__(self, nacos_config: dict):
        """
        :param nacos_config: Nacos 连接配置，必须含 url；可选 username/password/namespace
        """
        self.config = nacos_config
        self._token = None  # Nacos 2.x 认证 token 缓存（原模块级 _nacos_token）

    # ── 内部：登录认证 ──

    def _login(self) -> str:
        """
        登录 Nacos 获取 accessToken（原 _nacos_login 函数）。

        Nacos 2.x 需要认证，1.x 不需要。
        token 缓存到 self._token，后续请求复用。
        """
        if self._token:
            return self._token

        username = self.config.get("username", "")
        password = self.config.get("password", "")
        if not username:
            return ""

        p_url = urlparse(self.config["url"])
        conn = HTTPConnection(p_url.hostname, p_url.port, timeout=5)
        body = urlencode({"username": username, "password": password})
        conn.request(
            "POST",
            "/nacos/v1/auth/login",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r = conn.getresponse()
        data = json.loads(r.read().decode())
        conn.close()

        self._token = data.get("accessToken", "")
        return self._token

    # ── 公开：API 查询 ──

    def query(self, api_path: str, params: dict = None) -> dict:
        """
        调用 Nacos HTTP API（GET），自动处理认证与 token 过期重试（原 nacos_query 函数）。

        :param api_path: API 路径，如 "/nacos/v1/cs/configs"
        :param params:   查询参数字典
        :return:         解析后的 JSON 响应对像；若无法解析返回 {"raw": body}
        """
        if params is None:
            params = {}

        token = self._login()
        if token:
            params["accessToken"] = token

        p_url = urlparse(self.config["url"])
        qs = urlencode(params)
        path = f"{api_path}?{qs}" if qs else api_path

        conn = HTTPConnection(p_url.hostname, p_url.port, timeout=5)
        conn.request("GET", path)
        r = conn.getresponse()
        body = r.read().decode()
        conn.close()

        # token 过期 → 清空缓存、重新登录后重试一次
        if r.status == 403 and token:
            self._token = None
            token = self._login()
            if token:
                params["accessToken"] = token
                qs = urlencode(params)
                path = f"{api_path}?{qs}" if qs else api_path
                conn = HTTPConnection(p_url.hostname, p_url.port, timeout=5)
                conn.request("GET", path)
                r = conn.getresponse()
                body = r.read().decode()
                conn.close()

        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw": body}
