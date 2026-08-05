#!/usr/bin/env python3
"""
AIP Gateway 签名与调用模块。
从 server.py 提取，配置通过函数参数传入（不再依赖全局 GATEWAY 变量）。
"""

import hashlib
import base64
import json
import time
from urllib.parse import urlencode, urlparse
from http.client import HTTPConnection
from mcp.types import TextContent


def sign(gateway_config: dict, method: str, biz_json: str) -> tuple:
    """
    生成 AIP Gateway 请求签名。
    算法来源：ScrmGatewayUtils.java（AIP 工程生产代码）

    步骤：
        1. 组装待签名参数：app_id + charset + data(即 bizContent) + format + key + method + timestamp + version
        2. 按 key 字母序排序，用 & 拼接成字符串
        3. MD5 哈希 → 32 位小写十六进制
        4. 对十六进制字符串的 UTF-8 字节做 Base64 编码

    注意：签名中的 data 和 POST 请求体必须完全一致（包括换行符 \\r\\n、缩进空格），
          否则网关验签失败。Apifox 导出的 JSON 使用 \\r\\n 换行 + 4 空格缩进。

    参数：
        gateway_config — dict，包含 url / app_id / md5_key
        method         — 接口方法名，如 "nsos.getCg"
        biz_json       — 业务参数 JSON 字符串，如 '{"orgCode":"10201","billNo":"xxx"}'

    返回：(timestamp, sign) 元组
    """
    ts = str(int(time.time() * 1000))  # 毫秒级时间戳（网关要求）
    p = {
        "app_id":     gateway_config["app_id"],
        "charset":    "utf-8",
        "data":       biz_json,              # 注意：参数名叫 data，不是 bizContent
        "format":     "JSON",
        "key":        gateway_config["md5_key"],
        "method":     method,
        "timestamp":  ts,
        "version":    "1.0",
    }
    # 按 key 字典序排列，拼接为 "app_id=xxx&charset=utf-8&data=..." 格式
    content = "&".join(f"{k}={v}" for k, v in sorted(p.items()))
    # MD5 哈希 → 32 位小写十六进制
    md5 = hashlib.md5(content.encode()).hexdigest()
    # 对十六进制字符串的 UTF-8 字节做 Base64（网关验签时解码 base64 → hex → 比对 MD5）
    return ts, base64.b64encode(md5.encode()).decode()


def handle_gateway_call(arguments: dict, gateway_config: dict) -> TextContent:
    """
    处理 gateway_call 工具调用：签名并请求 Gateway，返回 JSON 响应。

    参数：
        arguments      — 工具调用参数，含 method（接口方法名）和 biz_content（业务参数 dict）
        gateway_config — dict，包含 url / app_id / md5_key，可选 style

    返回：TextContent（已序列化为 model_dump()）
    """
    method = arguments["method"]
    biz = arguments["biz_content"]

    # 序列化业务参数为 JSON，使用 4 空格缩进 + \\r\\n 换行（与 Apifox 一致）
    biz_json = json.dumps(biz, indent=4, ensure_ascii=False).replace("\n", "\r\n")

    # 生成时间戳和签名
    ts, sig = sign(gateway_config, method, biz_json)

    p = urlparse(gateway_config["url"])
    style = gateway_config.get("style", "aip")

    if style == "starbucks":
        # 星巴克 Gateway：method 在 URL query string，body 走 JSON（带签名）
        import urllib.request
        req_url = f"{gateway_config['url']}?method={method}"

        if isinstance(biz, list):
            # 数组参数：签名参数放 URL query string，body 纯数组 JSON
            qs_params = {
                "sign":      sig,
                "timestamp": ts,
                "app_id":    gateway_config["app_id"],
                "charset":   "utf-8",
                "format":    "JSON",
                "version":   "1.0",
            }
            qs = "&".join(f"{k}={v}" for k, v in qs_params.items())
            req_url = f"{gateway_config['url']}?method={method}&{qs}"
            req_body = json.dumps(biz, ensure_ascii=False).encode()
        else:
            # 对象参数：biz_content 字段展开到 body 顶层，与签名参数平级
            body_dict = {
                "sign":        sig,
                "timestamp":   ts,
                "app_id":      gateway_config["app_id"],
                "charset":     "utf-8",
                "format":      "JSON",
                "version":     "1.0",
            }
            body_dict.update(biz)
            req_body = json.dumps(body_dict, ensure_ascii=False).encode()

        req = urllib.request.Request(req_url, data=req_body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode())
    else:
        # AIP Gateway：全部参数走 form body（application/x-www-form-urlencoded）
        form_body = urlencode({
            "method":    method,
            "sign":      sig,
            "sign_type": "MD5",
            "app_id":    gateway_config["app_id"],
            "timestamp": ts,
            "charset":   "utf-8",
            "format":    "JSON",
            "version":   "1.0",
        }).encode() + b"&" + urlencode({"data": biz_json}).encode()

        conn = HTTPConnection(p.hostname, p.port, timeout=10)
        conn.request("POST", p.path, body=form_body,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        resp = json.loads(r.read().decode())
        conn.close()

    return TextContent(type="text", text=json.dumps(resp, indent=2, ensure_ascii=False)).model_dump()
