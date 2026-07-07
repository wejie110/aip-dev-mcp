# -*- coding: utf-8 -*-
"""
mysql_client.py — 极简 MySQL 客户端（仅 SELECT 查询）

来源：从 G:\histoneGitFiles\aip-dev-mcp\server.py 第 128–350 行完整提取。
原始文件：server.py
提取时间：2026-07-07

说明：
  通过原始 TCP socket 直接与 MySQL 服务端通信，实现：
    - mysql_native_password 认证握手
    - COM_QUERY 命令 + 文本协议结果集解析

  为什么不用 pymysql：WSL Python 3.14 无 pip，旧 venv 也没有。
  本实现仅依赖 Python 标准库（socket + struct + hashlib）。
"""

import struct
import socket
import hashlib


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
