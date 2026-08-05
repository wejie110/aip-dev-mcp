# -*- coding: utf-8 -*-
"""
mysql_client.py — MySQL 客户端（仅 SELECT 查询）

基于 pymysql 实现，支持 mysql_native_password 和 caching_sha2_password 认证。
兼容 MySQL 5.x / 8.x / MariaDB。

历史：原为纯 socket 实现（无外部依赖），现升级为 pymysql 以支持 MySQL 8 的
caching_sha2_password 认证。接口完全兼容，无需修改调用方。
"""

import pymysql
import pymysql.cursors


class MySQL:
    """
    MySQL 客户端，仅支持 SELECT 查询。

    用法：
        mysql = MySQL('10.18.8.157', 3306, 'aip', 'password', 'my_db')
        rows = mysql.query("SELECT * FROM some_table LIMIT 10")
        mysql.close()

    返回：
        list[dict]，每个元素是一行数据，key 为列名，value 为 Python 类型
        （str / int / float / None / Decimal）。
    """

    def __init__(self, host, port, user, password, database, timeout=5):
        """
        建立数据库连接。

        参数：
            host     — MySQL 服务器 IP
            port     — MySQL 端口，通常 3306
            user     — 用户名
            password — 密码
            database — 默认数据库（可为空字符串）
            timeout  — 连接超时秒数
        """
        # 如果 database 为空字符串，不传 database 参数
        db_kwarg = database if database else None

        self.conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=db_kwarg,
            connect_timeout=timeout,
            read_timeout=30,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            # 关键：关闭 SSL（内网环境无需加密，且避免证书问题）
            ssl=None,
        )

    def query(self, sql: str) -> list:
        """
        执行 SELECT 查询，返回字典列表。

        参数：
            sql — 仅支持 SELECT 语句（调用方 db_schema handler 已做校验）

        返回：
            list[dict]，每个元素是一行数据，key 为列名。
            数值类型返回 Python int/float，NULL 返回 None。
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
            return list(rows) if rows else []

    def close(self):
        """关闭连接"""
        try:
            self.conn.close()
        except Exception:
            pass
