# -*- coding: utf-8 -*-
"""
handlers/db_schema.py — db_schema 工具处理逻辑

从 server.py 第 693–722 行提取。
"""

import json
from mcp.types import TextContent
from clients.mysql_client import MySQL


def handle_db_schema(arguments: dict, connections: dict, databases: dict) -> TextContent:
    """
    处理 db_schema 工具调用：查询 MySQL 数据库（仅 SELECT）。

    参数：
        arguments  — 工具输入参数，必须包含 "db"（逻辑库名）和 "query"（SQL 语句）
        connections — 物理数据库连接配置，{连接名: {host, port, user, password}}
        databases   — 逻辑库名映射，{逻辑库名: {connection: 连接名, database: 物理库名}}

    返回：
        TextContent — 查询结果 JSON 或错误信息
    """
    db_name = arguments["db"]

    # 从配置中查找逻辑库名对应的连接信息和物理库名
    db_cfg = databases.get(db_name)
    if not db_cfg:
        return TextContent(type="text", text=json.dumps(
            {"error": f"未知数据库: {db_name}，已配置: {list(databases.keys())}"},
            ensure_ascii=False)).model_dump()

    conn_name = db_cfg["connection"]   # 物理连接名，如 "main-opr-db"
    conn_cfg = connections.get(conn_name)
    if not conn_cfg:
        return TextContent(type="text", text=json.dumps(
            {"error": f"未知连接: {conn_name}"}, ensure_ascii=False)).model_dump()

    # 建立连接 → 执行查询 → 关闭连接
    mysql = MySQL(conn_cfg["host"], conn_cfg["port"],
                  conn_cfg["user"], conn_cfg["password"],
                  db_cfg["database"])
    try:
        # ── 红线：仅允许 SELECT 查询，拒绝 INSERT/UPDATE/DELETE/DROP 等写操作 ──
        sql_raw = arguments["query"]
        sql_keyword = sql_raw.strip().upper()
        if not sql_keyword.startswith("SELECT"):
            return TextContent(type="text", text=json.dumps(
                {"error": f"只允许 SELECT 查询，禁止执行: {sql_raw[:50]}..."},
                ensure_ascii=False)).model_dump()

        rows = mysql.query(sql_raw)
        return TextContent(type="text", text=json.dumps(
            rows, indent=2, ensure_ascii=False, default=str)).model_dump()
    finally:
        mysql.close()  # 无论成功失败都关闭连接
