# ╔═════════════════════════════════════════════════════════════════════════════╗
# ║              Redis 客户端：纯 Python socket 实现（只读）                     ║
# ╚═════════════════════════════════════════════════════════════════════════════╝

import socket

# 允许的只读命令白名单
_REDIS_RO_COMMANDS = {
    "GET", "MGET", "HGET", "HGETALL", "HLEN", "HKEYS", "HVALS", "HEXISTS",
    "KEYS", "SCAN", "TYPE", "EXISTS", "TTL", "PTTL", "STRLEN",
    "LRANGE", "LLEN", "SMEMBERS", "SCARD", "ZRANGE", "ZRANK", "ZCARD",
    "DBSIZE", "INFO",
}


class RedisClient:
    """极简 Redis 客户端，仅支持只读命令。"""

    def __init__(self, host, port, password, db=0, timeout=5):
        self.sock = socket.create_connection((host, port), timeout)
        if password:
            self._cmd("AUTH", password)
        if db:
            self._cmd("SELECT", str(db))

    def _send(self, data: bytes):
        self.sock.sendall(data)

    def _recv(self) -> bytes:
        buf = bytearray()
        # Read until we have a complete RESP response
        # Simple: read first byte to determine type, then read accordingly
        first = self.sock.recv(1)
        if not first:
            return b""
        buf.extend(first)
        # Read rest based on type
        if first == b"+" or first == b"-" or first == b":":
            # Simple string / Error / Integer: read until \r\n
            while not buf.endswith(b"\r\n"):
                buf.extend(self.sock.recv(1))
        elif first == b"$":
            # Bulk string: read length line, then data
            while not buf.endswith(b"\r\n"):
                buf.extend(self.sock.recv(1))
            length = int(buf[1:-2])
            if length == -1:
                return buf  # Null
            # Read the string data + \r\n
            remaining = length + 2
            while remaining > 0:
                chunk = self.sock.recv(remaining)
                buf.extend(chunk)
                remaining -= len(chunk)
        elif first == b"*":
            # Array: read count line, then recurse for each element
            while not buf.endswith(b"\r\n"):
                buf.extend(self.sock.recv(1))
            count = int(buf[1:-2])
            if count == -1:
                return buf  # Null array
            for _ in range(count):
                buf.extend(self._recv())
        return bytes(buf)

    def _cmd(self, *args):
        parts = [f"*{len(args)}\r\n".encode()]
        for a in args:
            s = a.encode() if isinstance(a, str) else str(a).encode()
            parts.append(f"${len(s)}\r\n".encode() + s + b"\r\n")
        self._send(b"".join(parts))
        return self._recv()

    def execute(self, command: str) -> str:
        tokens = command.strip().split()
        if not tokens:
            return "-ERR empty command"
        cmd_key = tokens[0].upper()
        if cmd_key not in _REDIS_RO_COMMANDS:
            return f"-ERR 只允许只读命令，禁止: {cmd_key}"
        resp = self._cmd(*tokens)
        return self._format(resp)

    def _format(self, data: bytes) -> str:
        if data == b"$-1\r\n":
            return "(nil)"
        if data.startswith(b"*"):
            # Format array nicely
            parts = []
            pos = 1
            while pos < len(data):
                if data[pos:pos+1] == b"\r":
                    pos += 2
                    continue
                # Find end of this element
                element, consumed = self._parse_one(data, pos)
                if element is not None:
                    parts.append(element)
                pos += consumed
            return "\n".join(f"{i}) {v}" for i, v in enumerate(parts, 1))
        # Single element
        element, _ = self._parse_one(data, 0)
        return element or "(nil)"

    def _parse_one(self, data: bytes, pos: int):
        if pos >= len(data):
            return None, 0
        first = data[pos:pos+1]
        if first == b":":
            end = data.index(b"\r\n", pos)
            return data[pos+1:end].decode(), end + 2 - pos
        elif first == b"$":
            end = data.index(b"\r\n", pos)
            length = int(data[pos+1:end])
            if length == -1:
                return "(nil)", end + 2 - pos
            start = end + 2
            return data[start:start+length].decode(errors="replace"), start + length + 2 - pos
        elif first == b"+":
            end = data.index(b"\r\n", pos)
            return data[pos+1:end].decode(), end + 2 - pos
        elif first == b"-":
            end = data.index(b"\r\n", pos)
            return data[pos:end].decode(), end + 2 - pos
        return str(data[pos:pos+20]), 1

    def close(self):
        self.sock.close()
