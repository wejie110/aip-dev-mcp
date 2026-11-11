# -*- coding: utf-8 -*-
"""
handlers/oracle_query.py — oracle_query 工具处理逻辑

查询 Oracle 数据库（cmp6 等），仅允许 SELECT/WITH 查询。
逻辑与 db_schema 保持一致，返回结构增加 rowCount / truncated 便于判断分页。
"""

import json
from mcp.types import TextContent
from clients.oracle_client import Oracle


def handle_oracle_query(arguments: dict, connections: dict, databases: dict) -> dict:
    """
    处理 oracle_query 工具调用：查询 Oracle 数据库（仅 SELECT）。

    参数：
        arguments   — 工具输入参数，必须包含 "db"（逻辑库名）和 "query"（SQL 语句）
        connections — 物理连接配置，{连接名: {host, port, user, password, sid/service_name}}
        databases   — 逻辑库名映射，{逻辑库名: {connection: 连接名, schema: 默认 schema}}

    返回：
        TextContent — 查询结果 JSON 或错误信息
    """
    db_name = arguments["db"]

    db_cfg = databases.get(db_name)
    if not db_cfg:
        return TextContent(type="text", text=json.dumps(
            {"error": f"未知 Oracle 库: {db_name}，已配置: {list(databases.keys())}"},
            ensure_ascii=False)).model_dump()

    conn_name = db_cfg["connection"]
    conn_cfg = connections.get(conn_name)
    if not conn_cfg:
        return TextContent(type="text", text=json.dumps(
            {"error": f"未知连接: {conn_name}"}, ensure_ascii=False)).model_dump()

    # ── 红线：仅允许 SELECT / WITH 查询，拒绝 INSERT/UPDATE/DELETE/DROP 等写操作 ──
    sql_raw = arguments["query"]
    sql_keyword = sql_raw.strip().upper()
    if not (sql_keyword.startswith("SELECT") or sql_keyword.startswith("WITH")):
        return TextContent(type="text", text=json.dumps(
            {"error": f"只允许 SELECT/WITH 查询，禁止执行: {sql_raw[:50]}..."},
            ensure_ascii=False)).model_dump()

    try:
        ora = Oracle(conn_cfg["host"], int(conn_cfg["port"]),
                     conn_cfg["user"], conn_cfg["password"],
                     service_name=conn_cfg.get("service_name"),
                     sid=conn_cfg.get("sid"),
                     schema=db_cfg.get("schema"))
    except Exception as e:
        return TextContent(type="text", text=json.dumps(
            {"error": f"Oracle 连接失败: {type(e).__name__}: {e}"},
            ensure_ascii=False)).model_dump()

    try:
        rows, truncated = ora.query(sql_raw)
        return TextContent(type="text", text=json.dumps(
            {"rowCount": len(rows), "truncated": truncated, "rows": rows},
            indent=2, ensure_ascii=False, default=str)).model_dump()
    except Exception as e:
        return TextContent(type="text", text=json.dumps(
            {"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)).model_dump()
    finally:
        ora.close()
