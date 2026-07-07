# -*- coding: utf-8 -*-
"""
handlers/nacos_config.py — nacos_config 工具处理逻辑

从 server.py 第 724–764 行提取。
使用 NacosClient 替代原有的 nacos_query() 函数，配置通过函数参数传入。
"""

import json
from mcp.types import TextContent
from clients.nacos_client import NacosClient


def handle_nacos_config(arguments: dict, nacos_config: dict) -> TextContent:
    """
    处理 nacos_config 工具调用：查询 Nacos 配置中心（只读）。

    参数：
        arguments    — 工具输入参数，必须包含 "action"（config/search/service），
                       可选 dataId / group / keyword / serviceName
        nacos_config — Nacos 连接配置 dict，包含 url、可选 username/password/namespace

    返回：
        TextContent — 查询结果 JSON 或错误信息
    """
    action = arguments.get("action", "config")
    namespace = nacos_config.get("namespace", "")

    client = NacosClient(nacos_config)

    if action == "config":
        data_id = arguments.get("dataId", "")
        group = arguments.get("group", "DEFAULT_GROUP")
        if not data_id:
            return TextContent(type="text", text=json.dumps(
                {"error": "action=config 时需要 dataId 参数"}, ensure_ascii=False))
        result = client.query("/nacos/v1/cs/configs", {
            "dataId": data_id,
            "group": group,
            "tenant": namespace,
        })

    elif action == "search":
        keyword = arguments.get("keyword", "")
        if not keyword:
            return TextContent(type="text", text=json.dumps(
                {"error": "action=search 时需要 keyword 参数"}, ensure_ascii=False))
        result = client.query("/nacos/v1/cs/configs", {
            "search": "accurate",
            "dataId": keyword,
            "group": "",
            "tenant": namespace,
            "pageNo": 1,
            "pageSize": 50,
        })

    elif action == "service":
        service_name = arguments.get("serviceName", "")
        if not service_name:
            return TextContent(type="text", text=json.dumps(
                {"error": "action=service 时需要 serviceName 参数"}, ensure_ascii=False))
        result = client.query("/nacos/v1/ns/instance/list", {
            "serviceName": f"DEFAULT_GROUP@@{service_name}",
            "namespaceId": namespace,
        })

    else:
        return TextContent(type="text", text=json.dumps(
            {"error": f"未知 action: {action}，可选：config/search/service"}, ensure_ascii=False))

    return TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False)).model_dump()
