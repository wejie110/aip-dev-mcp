#!/usr/bin/env python3
"""
===============================================================================
 AIP Dev MCP Server — AI 助手开发环境集成服务
==============================================================================

 为 AI Agent（Hermes）提供开发环境工具：
   1. gateway_call — 调用 AIP Gateway 接口（内置 MD5 签名）
   2. db_schema    — 查询 MySQL 数据库（表结构、配置、业务数据）
   3. nacos_config — 查询 Nacos 配置中心（只读）
   4. redis_query  — 查询 Redis 缓存（只读）

 目录结构：
   server.py             ← 本文件：MCP 入口，配置加载，工具路由
   config.yaml           ← 配置文件
   clients/              ← 底层客户端（MySQL / Redis / Nacos）
   handlers/             ← 工具处理函数（gateway / db / nacos / redis）

 运行环境：旧版 Hermes venv（Python 3.11）
    /home/wejie110/.hermes.wsl.bak/hermes-agent/venv/bin/python3

 作者：红豆（Hermes）
 日期：2026-06-25 / 重构 2026-07-07
===============================================================================
"""
import json
import yaml
import os

# ── 配置加载 ──────────────────────────────────────────────────────────────────

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")
with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

GATEWAY = config["gateway"]
CONNS   = config.get("connections", {})
DBS     = config.get("databases", {})
NACOS   = config.get("nacos", {})
REDIS   = config.get("redis", {})

# ── 导入 handler 函数 ─────────────────────────────────────────────────────────

from handlers.gateway_call import handle_gateway_call
from handlers.db_schema    import handle_db_schema
from handlers.nacos_config  import handle_nacos_config
from handlers.redis_query   import handle_redis_query

# ── MCP Server ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent

    server = Server("aip-dev-mcp")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="gateway_call",
                description="调用 AIP Gateway 接口，自动签名。传入方法名和业务参数，返回 JSON 响应。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "method": {
                            "type": "string",
                            "description": "接口方法名。示例：nsos.getCg、rpt.shopSettleQuery"
                        },
                        "biz_content": {
                            "type": "object",
                            "description": "业务参数 JSON 对象。示例：{\"orgCode\":\"10201\",\"billNo\":\"JHDD20260624000016\"}"
                        },
                    },
                    "required": ["method", "biz_content"],
                }
            ),
            Tool(
                name="db_schema",
                description="查询 MySQL 数据库（仅 SELECT）。用于查表结构、配置项、业务数据。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "db": {
                            "type": "string",
                            "description": "逻辑数据库名，对应 config.yaml 中 databases 的 key。示例：aip4_nsos"
                        },
                        "query": {
                            "type": "string",
                            "description": "SQL SELECT 语句。示例：SELECT * FROM information_schema.COLUMNS WHERE TABLE_NAME='cnt'"
                        },
                    },
                    "required": ["db", "query"],
                }
            ),
            Tool(
                name="nacos_config",
                description="查询 Nacos 配置中心（只读，禁止增删改）。支持查单个配置、搜索配置、查服务实例。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "description": "操作类型：config / search / service",
                            "enum": ["config", "search", "service"]
                        },
                        "dataId": {
                            "type": "string",
                            "description": "配置 ID，如 aip-nsos.yml。action=config 时必填"
                        },
                        "group": {
                            "type": "string",
                            "description": "配置分组，默认 DEFAULT_GROUP",
                            "default": "DEFAULT_GROUP"
                        },
                        "keyword": {
                            "type": "string",
                            "description": "搜索关键字。action=search 时必填"
                        },
                        "serviceName": {
                            "type": "string",
                            "description": "服务名，如 aip-nsos。action=service 时必填"
                        },
                    },
                    "required": ["action"],
                }
            ),
            Tool(
                name="redis_query",
                description="查询 Redis 缓存（只读，禁止写入）。支持 GET/HGET/HGETALL/HLEN/KEYS/SCAN 等只读命令。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Redis 只读命令。示例：HGET aip4:intf-define shopSettleQuery"
                        },
                    },
                    "required": ["command"],
                }
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        if name == "gateway_call":
            return handle_gateway_call(arguments, GATEWAY)

        elif name == "db_schema":
            return handle_db_schema(arguments, CONNS, DBS)

        elif name == "nacos_config":
            return handle_nacos_config(arguments, NACOS)

        elif name == "redis_query":
            return handle_redis_query(arguments, REDIS)

        return TextContent(type="text", text=json.dumps(
            {"error": f"未知工具: {name}"}, ensure_ascii=False)).model_dump()

    async def main():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(main())
