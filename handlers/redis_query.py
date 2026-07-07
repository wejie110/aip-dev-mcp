# -*- coding: utf-8 -*-
"""
handlers/redis_query.py — redis_query 工具处理逻辑

从 server.py 第 766–777 行提取。
使用 RedisClient，配置通过函数参数传入（不再依赖全局 REDIS 变量）。
"""

from mcp.types import TextContent
from clients.redis_client import RedisClient


def handle_redis_query(arguments: dict, redis_config: dict) -> TextContent:
    """
    处理 redis_query 工具调用：查询 Redis 缓存（只读）。

    参数：
        arguments    — 工具输入参数，必须包含 "command"（Redis 只读命令）
        redis_config — Redis 连接配置 dict，包含 host / port / password / database

    返回：
        TextContent — 查询结果字符串
    """
    command = arguments["command"]

    rds = RedisClient(
        redis_config.get("host"),
        redis_config.get("port", 6379),
        redis_config.get("password", ""),
        redis_config.get("database", 0),
    )

    try:
        resp = rds.execute(command)
        return TextContent(type="text", text=resp).model_dump()
    finally:
        rds.close()
