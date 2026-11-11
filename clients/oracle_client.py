# -*- coding: utf-8 -*-
"""
oracle_client.py — Oracle 客户端（仅 SELECT 查询）

基于 python-oracledb **thick 模式**（thin 模式不支持本项目的老版本 Oracle 服务端，
报 DPY-3010），运行时依赖 Oracle Instant Client 19。

Instant Client 位置（可用环境变量 ORACLE_CLIENT_LIB_DIR 覆盖）：
    /home/wejie/.hermes-tools-store/instantclient_19_28

注意：basiclite 依赖 libaio.so.1，Ubuntu 24 起系统只提供 libaio.so.1t64，
已在 IC 目录内做了 libaio.so.1 软链；因为 ld.so 在进程启动时缓存库搜索路径，
调用方需保证 LD_LIBRARY_PATH 已包含 IC 目录（server.py 启动时已做 re-exec 处理）。
"""

import os
import oracledb

DEFAULT_LIB_DIR = os.environ.get(
    "ORACLE_CLIENT_LIB_DIR", "/home/wejie/.hermes-tools-store/instantclient_19_28")

_initialized = False


def _init_client(lib_dir=None):
    """初始化 thick 模式（进程内只初始化一次）"""
    global _initialized
    if _initialized:
        return
    d = lib_dir or DEFAULT_LIB_DIR
    try:
        if os.path.isdir(d):
            oracledb.init_oracle_client(lib_dir=d)
        else:
            oracledb.init_oracle_client()
    except Exception as e:
        # DPI-1050：重复初始化，忽略；其它异常继续抛给调用方
        if "DPI-1050" not in str(e):
            raise
    _initialized = True


class Oracle:
    """
    Oracle 客户端，仅支持 SELECT 查询。

    用法：
        ora = Oracle("10.16.8.22", 1521, "hicmpyznew", "hicmpyznew", sid="rdtest", schema="HICMPYZNEW")
        rows, truncated = ora.query("SELECT * FROM TPURSTKMAXMIN WHERE ROWNUM <= 10")
        ora.close()
    """

    def __init__(self, host, port, user, password,
                 service_name=None, sid=None, schema=None, timeout=10):
        _init_client()
        if sid:
            dsn = oracledb.makedsn(host, port, sid=sid)
        elif service_name:
            dsn = oracledb.makedsn(host, port, service_name=service_name)
        else:
            raise ValueError("Oracle 连接需配置 service_name 或 sid")
        self.conn = oracledb.connect(user=user, password=password, dsn=dsn)
        # 会话级默认 schema，查询可不带 schema 前缀
        if schema:
            cur = self.conn.cursor()
            try:
                cur.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {schema}")
            finally:
                cur.close()

    def query(self, sql: str, max_rows: int = 500):
        """
        执行 SELECT 查询。

        返回：
            (rows, truncated) — rows 为 list[dict]（列名大写，与 Oracle 一致），
            truncated 表示结果是否超过 max_rows 被截断。
        """
        cur = self.conn.cursor()
        try:
            cur.execute(sql)
            if cur.description is None:
                return [], False
            cols = [d[0] for d in cur.description]
            fetched = cur.fetchmany(max_rows + 1)
            truncated = len(fetched) > max_rows
            rows = [dict(zip(cols, r)) for r in fetched[:max_rows]]
            return rows, truncated
        finally:
            cur.close()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
