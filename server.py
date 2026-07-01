#!/usr/bin/env python3
"""
================================================================================
 AIP Dev MCP Server — AI 助手开发环境集成服务
================================================================================

 为 AI Agent（Hermes / Claude Code）提供两大能力：
   1. gateway_call — 调用 AIP Gateway 接口（内置 MD5 签名）
   2. db_schema    — 查询 MySQL 数据库（表结构、配置、业务数据）

 运行环境：旧版 Hermes venv（Python 3.11）
    /home/wejie110/.hermes.wsl.bak/hermes-agent/venv/bin/python3

 零外部依赖（仅需 Python 标准库 + 旧 venv 自带的 mcp 包）：
   - HTTP 请求：标准库 http.client（无需 requests）
   - MySQL 协议：纯 socket 实现 mysql_native_password 认证 + 文本协议查询
   - MCP 协议：旧版 mcp 包（mcp.server.Server + mcp.server.stdio）

 签名算法来源：ScrmGatewayUtils.java（AIP 工程生产代码）
   将所有请求参数按字母序排列，拼接 &，末尾加 key=<md5Key>，MD5 → base64

 作者：红豆（Hermes）
 日期：2026-06-25
================================================================================
"""
import hashlib    # MD5 签名计算
import base64     # 签名 Base64 编码
import json       # JSON 解析与序列化
import time       # 生成毫秒级时间戳
import yaml       # 读取 config.yaml 配置文件
import os         # 拼接配置文件路径
import struct     # MySQL 协议二进制数据打包
import socket     # MySQL 原始 TCP 连接
from urllib.parse import urlencode, urlparse  # HTTP URL 拼接与解析
from http.client import HTTPConnection         # 标准库 HTTP 客户端（替代 requests）

# ──────────────────────────────────────────────────────────────────────────────
# 配置加载
# ──────────────────────────────────────────────────────────────────────────────

# 配置文件与 server.py 同目录
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")
with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

GATEWAY = config["gateway"]           # Gateway 连接信息（URL + 签名参数）
CONNS   = config.get("connections", {})  # 物理数据库连接池（host/port/user/password）
DBS     = config.get("databases", {})    # 逻辑库名 → (连接名 + 物理库名) 映射
NACOS   = config.get("nacos", {})         # Nacos 配置中心（只读查询）


# ╔═════════════════════════════════════════════════════════════════════════════╗
# ║                        HTTP 工具：发送 POST 请求                            ║
# ╚═════════════════════════════════════════════════════════════════════════════╝

def http_post(url: str, params: dict, body_bytes: bytes) -> dict:
    """
    发送 HTTP POST 请求到 AIP Gateway。
    使用标准库 http.client，无需安装 requests。

    参数：
        url        — Gateway 地址，如 http://10.18.8.195:9999/aip-gateway/gateway.action
        params     — URL 查询参数（method, sign, timestamp 等）
        body_bytes — 请求体（JSON 字符串的 UTF-8 字节）

    返回：解析后的 JSON 响应字典
    """
    p = urlparse(url)                                      # 解析主机名和端口
    conn = HTTPConnection(p.hostname, p.port, timeout=10)  # 建立连接，10 秒超时
    # 拼接 URL 查询字符串并发送 POST
    conn.request("POST", f"{p.path}?{urlencode(params)}",
                 body=body_bytes,
                 headers={"Content-Type": "application/json"})
    r = conn.getresponse()
    data = r.read().decode()   # 读取响应体
    conn.close()               # 关闭连接（非连接池模式）
    return json.loads(data)    # 解析 JSON 返回


# ╔═════════════════════════════════════════════════════════════════════════════╗
# ║                    AIP Gateway MD5 签名算法                                ║
# ╚═════════════════════════════════════════════════════════════════════════════╝

def sign(method: str, biz_json: str) -> tuple:
    """
    生成 AIP Gateway 请求签名。
    算法来源：ScrmGatewayUtils.java（AIP 工程生产代码）

    步骤：
        1. 组装待签名参数：app_id + charset + data(即 bizContent) + format + key + method + timestamp + version
        2. 按 key 字母序排序，用 & 拼接成字符串
        3. MD5 哈希 → 32 位小写十六进制
        4. 对十六进制字符串的 UTF-8 字节做 Base64 编码

    注意：签名中的 data 和 POST 请求体必须完全一致（包括换行符 \r\n、缩进空格），
          否则网关验签失败。Apifox 导出的 JSON 使用 \r\n 换行 + 4 空格缩进。

    参数：
        method   — 接口方法名，如 "nsos.getCg"
        biz_json — 业务参数 JSON 字符串，如 '{"orgCode":"10201","billNo":"xxx"}'

    返回：(timestamp, sign) 元组
    """
    ts = str(int(time.time() * 1000))  # 毫秒级时间戳（网关要求）
    p = {
        "app_id":     GATEWAY["app_id"],     # "951413"
        "charset":    "utf-8",
        "data":       biz_json,              # 注意：参数名叫 data，不是 bizContent
        "format":     "JSON",
        "key":        GATEWAY["md5_key"],    # "321513"，从 Redis aip4:app-access 获取
        "method":     method,                # 如 "nsos.getCg"
        "timestamp":  ts,
        "version":    "1.0",
    }
    # 按 key 字典序排列，拼接为 "app_id=xxx&charset=utf-8&data=..." 格式
    content = "&".join(f"{k}={v}" for k, v in sorted(p.items()))
    # MD5 哈希 → 32 位小写十六进制
    md5 = hashlib.md5(content.encode()).hexdigest()
    # 对十六进制字符串的 UTF-8 字节做 Base64（网关验签时解码 base64 → hex → 比对 MD5）
    return ts, base64.b64encode(md5.encode()).decode()


# ╔═════════════════════════════════════════════════════════════════════════════╗
# ║              MySQL 客户端：纯 Python socket 实现（零外部依赖）               ║
# ╚═════════════════════════════════════════════════════════════════════════════╝

class MySQL:
    """
    极简 MySQL 客户端，仅支持 SELECT 查询。
    通过原始 TCP socket 直接与 MySQL 服务端通信，实现：
      - mysql_native_password 认证握手
      - COM_QUERY 命令 + 文本协议结果集解析

    为什么不用 pymysql：WSL Python 3.14 无 pip，旧 venv 也没有。
    这个实现仅依赖 Python 标准库（socket + struct + hashlib）。
    """

    def __init__(self, host, port, user, password, database, timeout=5):
        """
        建立连接并完成认证。

        参数：
            host     — MySQL 服务器 IP
            port     — MySQL 端口，通常 3306
            user     — 用户名
            password — 密码（mysql_native_password 认证）
            database — 默认数据库（连接后 USE xxx）
            timeout  — TCP 连接超时秒数
        """
        self.sock = socket.create_connection((host, port), timeout)  # TCP 三次握手
        self.seq = 0        # MySQL 包序号（每个方向独立计数，0~255 循环）
        self._auth(user, password, database)  # 握手认证

    # ── 底层：发送/接收 MySQL 协议包 ──

    def _send(self, data: bytes):
        """
        发送一个 MySQL 协议包。
        格式：[3 字节长度][1 字节序号][载荷数据]
        长度最大 16MB（0xFFFFFF），本实现不处理分包（查询简单够用）。
        """
        # 提取 4 字节长度的低 3 字节 + 序号
        self.sock.sendall(struct.pack('<I', len(data))[:3] + bytes([self.seq]) + data)
        self.seq = (self.seq + 1) % 256  # 序号递增，256 循环

    def _recv(self) -> bytes:
        """
        接收一个 MySQL 协议包。
        先读 4 字节头（3 长度 + 1 序号），再根据长度读完整载荷。
        更新序号为包头中的序号 + 1（服务端序号跟踪）。
        """
        hdr = self.sock.recv(4)                     # 包头：3 字节长度 + 1 字节序号
        if len(hdr) < 4:
            return None
        length = hdr[0] | (hdr[1] << 8) | (hdr[2] << 16)  # 小端 3 字节 → 整数
        self.seq = hdr[3] + 1                       # 同步服务端序号
        # 循环读取直到凑够 length 字节（处理 TCP 分包）
        buf = bytearray()
        while len(buf) < length:
            chunk = self.sock.recv(length - len(buf))
            if not chunk:
                break
            buf.extend(chunk)
        return bytes(buf)

    # ── 认证握手：mysql_native_password ──

    def _auth(self, user, password, database):
        """
        MySQL 握手认证（mysql_native_password 协议）。

        流程：
            1. 接收服务端握手包，提取 20 字节 salt
            2. 计算认证响应：
               hash1 = SHA1(password)
               hash2 = SHA1(hash1)
               auth  = XOR(hash1, SHA1(salt + hash2))
            3. 发送握手响应
            4. 检查服务端返回 OK 包

        mysql_native_password 仅在 MySQL 8.0 之前为默认插件。
        MySQL 8.0+ 默认 caching_sha2_password，但大多数生产环境
        仍配置 native_password 兼容旧客户端。
        """
        # 第一步：接收握手包
        pkt = self._recv()

        # 从握手包中解析 salt
        # 握手包结构（简化）：
        #   [1B 协议版本][变长 服务端版本][4B 线程ID][8B salt前半][1B 填充]
        #   [2B 能力标志][1B 字符集][2B 状态][2B 能力高位][1B 认证插件长度]
        #   [10B 保留][变长 salt后半][变长 认证插件名]
        idx = 1
        while pkt[idx] != 0:           # 跳过服务端版本字符串（以 \0 结尾）
            idx += 1
        idx += 1 + 4                   # 跳过 \0 + thread_id(4字节)
        salt1 = pkt[idx:idx + 8]       # salt 前半：8 字节
        idx += 8 + 1                   # 跳过 filler
        idx += 2 + 1 + 2 + 2          # 跳过 能力标志(2) + 字符集(1) + 状态(2) + 能力高位(2)
        auth_len = pkt[idx]            # 认证插件名长度
        idx += 1 + 10                  # 跳过 + 保留字段(10)
        salt2_end = idx
        while pkt[salt2_end] != 0:     # 找到 salt 后半的 \0 结尾
            salt2_end += 1
        salt = salt1 + pkt[idx:salt2_end]  # 合并成完整 salt

        # 第二步：计算认证响应
        sha1 = hashlib.sha1
        h1 = sha1(password.encode()).digest()                     # SHA1(密码明文)
        h2 = sha1(h1).digest()                                    # SHA1(SHA1(密码))
        # auth = XOR( SHA1(salt + h2), h1 )
        auth = bytes([a ^ b for a, b in zip(sha1(salt[:20] + h2).digest(), h1)])

        # 第三步：组装握手响应包
        # 能力标志：0x0fa28d = CLIENT_LONG_PASSWORD | CLIENT_PLUGIN_AUTH |
        #   CLIENT_SECURE_CONNECTION | CLIENT_PROTOCOL_41 | CLIENT_CONNECT_WITH_DB
        caps = 0x0fa28d
        buf = struct.pack('<I', caps)      # 客户端能力标志
        buf += struct.pack('<I', 16777215) # 最大包大小 16MB
        buf += b'\x21'                     # 字符集 utf8_general_ci
        buf += b'\x00' * 23                # 保留填充
        buf += user.encode() + b'\x00'     # 用户名（null-terminated）
        buf += struct.pack('B', 20) + auth # 认证数据长度(20) + 认证响应
        if database:
            buf += database.encode() + b'\x00'  # 默认数据库（null-terminated）
        buf += b'mysql_native_password\x00'     # 认证插件名
        self._send(buf)

        # 第四步：检查服务端响应
        resp = self._recv()
        if resp[0] != 0:  # 0x00 = OK, 0xFF = Error
            err = resp[3:].decode(errors='replace')  # 错误消息在包头之后
            raise Exception(f"MySQL auth failed: {err}")

    # ── 查询：COM_QUERY + 文本协议结果集解析 ──

    def query(self, sql: str) -> list:
        """
        执行 SELECT 查询，返回字典列表。
        仅支持文本协议（COM_QUERY），不支持预处理语句。

        文本协议结果集格式：
            列数量包  → [列数]
            列定义包  → [catalog][schema][table][org_table][name][org_name]...
            EOF 包    → 列定义结束标记
            数据行包  → [长度编码字符串] × 列数
            EOF 包    → 数据结束标记（或 OK 包表示影响行数）

        参数：
            sql — 仅支持 SELECT 语句

        返回：
            list[dict]，每个元素是一行数据，key 为列名，value 为字符串或 None
        """
        self.seq = 0  # 每次新查询重置包序号

        # 发送 COM_QUERY 命令（命令字节 0x03 + SQL 语句）
        self._send(b'\x03' + sql.encode())

        # 接收第一个包：判断响应类型
        pkt = self._recv()
        if pkt[0] == 0x00:  # OK 包（无结果集，如 UPDATE/DELETE）
            return []
        if pkt[0] == 0xff:  # ERR 包（SQL 语法错误等）
            raise Exception(f"MySQL error: {pkt[3:].decode(errors='replace')}")

        # ── 解析列定义 ──
        col_count = pkt[0]  # 列数
        cols = []
        for _ in range(col_count):
            cp = self._recv()
            # 列定义包结构（简化解析，只提取列名）：
            #   [1B catalog_len][var catalog][1B schema_len][var schema]
            #   [1B table_len][var table][1B org_table_len][var org_table]
            #   [1B name_len][var name]...
            # 我们跳过前 4 个分段，取 name（实际列名）
            pos = 1 + cp[0]         # 跳过 catalog
            pos += 1 + cp[pos]      # 跳过 schema
            pos += 1 + cp[pos]      # 跳过 table
            pos += 1 + cp[pos]      # 跳过 org_table
            name_len = cp[pos]      # 列名长度
            pos += 1
            name = cp[pos:pos + name_len].decode()  # 列名字符串
            cols.append(name)
        self._recv()  # 接收 EOF 包（列定义结束）

        # ── 解析数据行 ──
        results = []
        while True:
            rp = self._recv()  # 接收一行数据
            if len(rp) <= 5 and rp[0] == 0xfe:
                break  # EOF 包，数据结束

            row = {}
            pos = 0
            for c in cols:
                # MySQL 长度编码整数 (Length-Encoded Integer)：
                #   <  0xFB    → NULL
                #   <  251     → 1 字节长度
                #   == 0xFC    → 2 字节长度（小端）
                #   == 0xFD    → 3 字节长度（小端）
                #   == 0xFE    → 8 字节长度（小端，本实现不处理）
                first = rp[pos]
                if first < 251:
                    l = first
                    pos += 1
                elif first == 0xfc:
                    l = struct.unpack('<H', rp[pos+1:pos+3])[0]  # uint16 小端
                    pos += 3
                elif first == 0xfd:
                    l = struct.unpack('<I', rp[pos+1:pos+4] + b'\x00')[0]  # uint24 → uint32
                    pos += 4
                elif first == 0xfb:
                    row[c] = None   # NULL
                    pos += 1
                    continue
                else:  # 0xFE → 8字节长度，极少出现，按 NULL 处理
                    row[c] = None
                    pos += 1
                    continue
                # 读取 l 字节的数据，解码为字符串
                row[c] = rp[pos:pos + l].decode(errors='replace') if l > 0 else ""
                pos += l
            results.append(row)
        return results

    def close(self):
        """关闭 TCP 连接"""
        self.sock.close()


# ╔═════════════════════════════════════════════════════════════════════════════╗
# ║                    Nacos 配置中心客户端（只读）                              ║
# ╚═════════════════════════════════════════════════════════════════════════════╝

# Nacos 2.x 认证 token 缓存（登录一次，复用）
_nacos_token = None

def _nacos_login() -> str:
    """
    登录 Nacos 获取 accessToken。
    Nacos 2.x 需要认证，1.x 不需要。先尝试无 token 请求，
    若返回 403 则登录获取 token 后重试。
    """
    global _nacos_token
    if _nacos_token:
        return _nacos_token
    u = NACOS.get("username", "")
    p = NACOS.get("password", "")
    if not u:
        return ""
    p_url = urlparse(NACOS["url"])
    conn = HTTPConnection(p_url.hostname, p_url.port, timeout=5)
    body = urlencode({"username": u, "password": p})
    conn.request("POST", "/nacos/v1/auth/login",
                 body=body,
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    r = conn.getresponse()
    data = json.loads(r.read().decode())
    conn.close()
    _nacos_token = data.get("accessToken", "")
    return _nacos_token


def nacos_query(api_path: str, params: dict = None) -> dict:
    """
    调用 Nacos HTTP API（GET），自动处理认证。
    若返回 403 则重新登录后重试一次。
    """
    if params is None:
        params = {}
    token = _nacos_login()
    if token:
        params["accessToken"] = token
    p_url = urlparse(NACOS["url"])
    qs = urlencode(params)
    path = f"{api_path}?{qs}" if qs else api_path
    conn = HTTPConnection(p_url.hostname, p_url.port, timeout=5)
    conn.request("GET", path)
    r = conn.getresponse()
    body = r.read().decode()
    conn.close()
    # 若 token 过期，重新登录后重试
    if r.status == 403 and token:
        global _nacos_token
        _nacos_token = None
        token = _nacos_login()
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


# ╔═════════════════════════════════════════════════════════════════════════════╗
# ║                       MCP Server：工具注册与调用                            ║
# ╚═════════════════════════════════════════════════════════════════════════════╝

if __name__ == "__main__":
    import asyncio
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    
    # 创建 MCP Server 实例，名称为 "aip-dev-mcp"
    server = Server("aip-dev-mcp")
    
    
    @server.list_tools()
    async def list_tools():
        """
        向 MCP 客户端（Hermes）声明可用工具列表。
        每个工具包含名称、描述、输入参数 schema。
        """
        return [
            Tool(
                name="gateway_call",
                description="调用 AIP Gateway 接口，自动签名。传入方法名和业务参数，返回 JSON 响应。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "method": {
                            "type": "string",
                            "description": "接口方法名。示例：nsos.getCg、nsos.getZcys、nsos.getCntSupplierList"
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
                            "description": "操作类型：config（查单个配置，需 dataId+group）、search（搜索配置，需 keyword）、service（查服务实例，需 serviceName）",
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
        ]
    
    
    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        """
        处理 MCP 客户端发来的工具调用请求。
        根据工具名称分发到对应处理器。
        """
        # ── gateway_call：调用 AIP Gateway ──
        if name == "gateway_call":
            method = arguments["method"]
            biz = arguments["biz_content"]
            # 序列化业务参数为 JSON，使用 4 空格缩进 + \r\n 换行（与 Apifox 一致）
            biz_json = json.dumps(biz, indent=4, ensure_ascii=False).replace("\n", "\r\n")
            # 生成时间戳和签名
            ts, sig = sign(method, biz_json)
            # 发送 POST 请求到 Gateway
            resp = http_post(
                GATEWAY["url"],
                {
                    "method":    method,
                    "sign":      sig,
                    "sign_type": "MD5",
                    "app_id":    GATEWAY["app_id"],
                    "timestamp": ts,
                    "charset":   "utf-8",
                    "format":    "JSON",
                    "version":   "1.0",
                },
                biz_json.encode()  # JSON 字符串 → UTF-8 字节
            )
            return TextContent(type="text", text=json.dumps(resp, indent=2, ensure_ascii=False)).model_dump()
    
        # ── db_schema：查询 MySQL ──
        elif name == "db_schema":
            db_name = arguments["db"]
            # 从配置中查找逻辑库名对应的连接信息和物理库名
            db_cfg = DBS.get(db_name)
            if not db_cfg:
                return TextContent(type="text", text=json.dumps(
                    {"error": f"未知数据库: {db_name}，已配置: {list(DBS.keys())}"},
                    ensure_ascii=False)).model_dump()
            conn_name = db_cfg["connection"]   # 物理连接名，如 "main-opr-db"
            conn_cfg = CONNS.get(conn_name)
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
                return TextContent(type="text", text=json.dumps(rows, indent=2, ensure_ascii=False, default=str)).model_dump()
            finally:
                mysql.close()  # 无论成功失败都关闭连接
    
        # ── nacos_config：查询 Nacos 配置中心（只读）──
        elif name == "nacos_config":
            action = arguments.get("action", "config")
            namespace = NACOS.get("namespace", "")
            if action == "config":
                data_id = arguments.get("dataId", "")
                group = arguments.get("group", "DEFAULT_GROUP")
                if not data_id:
                    return TextContent(type="text", text=json.dumps(
                        {"error": "action=config 时需要 dataId 参数"}, ensure_ascii=False)).model_dump()
                result = nacos_query("/nacos/v1/cs/configs", {
                    "dataId": data_id,
                    "group": group,
                    "tenant": namespace,
                })
            elif action == "search":
                keyword = arguments.get("keyword", "")
                if not keyword:
                    return TextContent(type="text", text=json.dumps(
                        {"error": "action=search 时需要 keyword 参数"}, ensure_ascii=False)).model_dump()
                result = nacos_query("/nacos/v1/cs/configs", {
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
                        {"error": "action=service 时需要 serviceName 参数"}, ensure_ascii=False)).model_dump()
                result = nacos_query("/nacos/v1/ns/instance/list", {
                    "serviceName": f"DEFAULT_GROUP@@{service_name}",
                    "namespaceId": namespace,
                })
            else:
                return TextContent(type="text", text=json.dumps(
                    {"error": f"未知 action: {action}，可选：config/search/service"}, ensure_ascii=False)).model_dump()
            return TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False)).model_dump()
    
        # 未知工具
        return TextContent(type="text", text=json.dumps(
            {"error": f"未知工具: {name}"}, ensure_ascii=False)).model_dump()
    
    
    async def main():
        """
        MCP Server 入口：通过标准输入/输出（stdio）与 MCP 客户端通信。
        客户端启动本脚本后，通过 stdin 发送 JSON-RPC 请求，stdout 返回响应。
        """
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    
    
    asyncio.run(main())
    
