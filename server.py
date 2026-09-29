#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 AIP Dev MCP Server — AI 助手开发环境集成服务（MCP 2.0 版）
==============================================================================

 为 AI Agent（Hermes / OpenClaw / Cursor）提供开发环境工具：
   1. gateway_call           — 调用 AIP Gateway 接口（内置 MD5 签名）
   2. starbucks_gateway_call — 调用星巴克(NGBE) Gateway 接口（同一签名算法）
   3. db_schema              — 查询 MySQL 数据库（表结构、配置、业务数据）
   4. nacos_config           — 查询 Nacos 配置中心（只读）
   5. redis_query            — 查询 Redis 缓存（只读）

 目录结构：
   server.py             ← 本文件：MCP 入口，配置加载，工具注册（MCP 2.0）
   config.yaml           ← 配置文件
   clients/              ← 底层客户端（MySQL / Redis / Nacos）
   handlers/             ← 工具处理函数（gateway / db / nacos / redis）

 SDK 说明：
   - 适配 MCP SDK 2.x（mcp>=2.0.0），使用新高层 API mcp.server.MCPServer
     （@server.tool() 注册 + server.run() 启动），不再使用 1.x 的
     @server.list_tools()/@server.call_tool() 低层装饰器。
   - handlers/ 与 clients/ 业务层零改动：handler 仍返回
     TextContent.model_dump() dict，由本文件 _wrap_text() 适配为
     mcp.types.CallToolResult。
   - 2026-09-02 由 mcp 1.29.1 迁移至 mcp 2.x。

 运行环境（Windows）：必须用本仓库自带的 venv（内含 mcp 2.x），不要用其它解释器
   C:/Users/wejie/Documents/aip-dev-mcp/venv/Scripts/python.exe -X utf8 server.py

   -X utf8 必须保留：config.yaml 为 UTF-8，Windows 中文系统默认 GBK，
     不加会直接 UnicodeDecodeError 退出（2026-09-29 已复现）。
   - 依赖版本以 venv 内实际安装为准：pip install -r requirements.txt

 MCP 客户端配置（Hermes ~/.hermes/config.yaml，Windows 路径此处用正斜杠书写，
 写进 config.yaml 时用反斜杠同样有效）：
   aip_dev:
     command: /mnt/c/Windows/System32/cmd.exe
     args:
     - /c
     - C:/Users/wejie/Documents/aip-dev-mcp/venv/Scripts/python.exe -X utf8
       C:/Users/wejie/Documents/aip-dev-mcp/server.py

 作者：红豆（Hermes） + 迁移 2026-09-02 / 路径校正 2026-09-29
===============================================================================
"""
import json
import yaml
import os

# ── 配置加载 ──────────────────────────────────────────────────────────────────

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")
with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

GATEWAY           = config["gateway"]
STARBUCKS_GATEWAY = config.get("starbucks_gateway", {})
CONNS   = config.get("connections", {})
DBS     = config.get("databases", {})
NACOS   = config.get("nacos", {})
REDIS   = config.get("redis", {})

# ── 导入 handler 函数（业务层，与 1.x 版完全一致） ─────────────────────────────

from handlers.gateway_call import handle_gateway_call
from handlers.db_schema    import handle_db_schema
from handlers.nacos_config  import handle_nacos_config
from handlers.redis_query   import handle_redis_query


# ── MCP 2.0 Server ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from typing import Annotated, Literal, Union
    from pydantic import Field
    from mcp.server import MCPServer
    from mcp.types import CallToolResult, TextContent

    def _wrap_text(result: dict) -> CallToolResult:
        """
        handlers 返回 TextContent.model_dump() dict（{"type":"text","text":...}），
        适配为 MCP 2.0 的 CallToolResult。
        """
        if isinstance(result, dict) and result.get("type") == "text":
            return CallToolResult(content=[TextContent(type="text", text=result.get("text", ""))])
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(
            result, indent=2, ensure_ascii=False, default=str))])

    server = MCPServer("aip-dev-mcp", version="2.0.0")

    @server.tool(
        name="gateway_call",
        description="调用 AIP Gateway 接口，自动签名。传入方法名和业务参数，返回 JSON 响应。",
    )
    def gateway_call(
        method: Annotated[str, Field(
            description="接口方法名。示例：nsos.getCg、rpt.shopSettleQuery")],
        biz_content: Annotated[dict, Field(
            description="业务参数 JSON 对象。示例：{\"orgCode\":\"10201\",\"billNo\":\"JHDD20260624000016\"}")],
    ) -> CallToolResult:
        return _wrap_text(handle_gateway_call(
            {"method": method, "biz_content": biz_content}, GATEWAY))

    @server.tool(
        name="starbucks_gateway_call",
        description="调用星巴克(NGBE) Gateway 接口，自动签名。传入方法名和业务参数，返回 JSON 响应。签名算法与 AIP 一致，仅 IP/端口不同（10.18.8.157:9889）。",
    )
    def starbucks_gateway_call(
        method: Annotated[str, Field(
            description="接口方法名。示例：sale.posConfig、nsos.getCg")],
        biz_content: Annotated[Union[dict, list], Field(
            description="业务参数 JSON 对象或数组。对象示例：{\"orgCode\":\"10201\"}；数组示例：[{\"msgId\":\"xxx\",\"shopCode\":\"C00001\"}]")],
    ) -> CallToolResult:
        return _wrap_text(handle_gateway_call(
            {"method": method, "biz_content": biz_content}, STARBUCKS_GATEWAY))

    @server.tool(
        name="db_schema",
        description="查询 MySQL 数据库（仅 SELECT）。用于查表结构、配置项、业务数据。",
    )
    def db_schema(
        db: Annotated[str, Field(
            description="逻辑数据库名，对应 config.yaml 中 databases 的 key。示例：aip4_nsos")],
        query: Annotated[str, Field(
            description="SQL SELECT 语句。示例：SELECT * FROM information_schema.COLUMNS WHERE TABLE_NAME='cnt'")],
    ) -> CallToolResult:
        return _wrap_text(handle_db_schema({"db": db, "query": query}, CONNS, DBS))

    @server.tool(
        name="nacos_config",
        description="查询 Nacos 配置中心（只读，禁止增删改）。支持查单个配置、搜索配置、查服务实例。",
    )
    def nacos_config(
        action: Annotated[Literal["config", "search", "service"], Field(
            description="操作类型：config / search / service")],
        dataId: Annotated[str | None, Field(
            description="配置 ID，如 aip-nsos.yml。action=config 时必填")] = None,
        group: Annotated[str, Field(
            description="配置分组，默认 DEFAULT_GROUP")] = "DEFAULT_GROUP",
        keyword: Annotated[str | None, Field(
            description="搜索关键字。action=search 时必填")] = None,
        serviceName: Annotated[str | None, Field(
            description="服务名，如 aip-nsos。action=service 时必填")] = None,
    ) -> CallToolResult:
        return _wrap_text(handle_nacos_config(
            {"action": action, "dataId": dataId, "group": group,
             "keyword": keyword, "serviceName": serviceName}, NACOS))

    @server.tool(
        name="redis_query",
        description="查询 Redis 缓存（只读，禁止写入）。支持 GET/HGET/HGETALL/HLEN/KEYS/SCAN 等只读命令。",
    )
    def redis_query(
        command: Annotated[str, Field(
            description="Redis 只读命令。示例：HGET aip4:intf-define shopSettleQuery")],
    ) -> CallToolResult:
        return _wrap_text(handle_redis_query({"command": command}, REDIS))

    # 同步阻塞运行：stdio 传输（默认 transport="stdio"）
    server.run()
