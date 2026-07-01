# AIP Dev MCP Server 技术方案

**需求提出人：** 刘伟杰
**需求提出日期：** 2026-06-25
**设计人：** 红豆
**设计日期：** 2026-06-25

---

# 需求描述

## 背景

当前 AI 辅助开发中存在以下效率瓶颈：

1. **API 调用依赖 Apifox 手动导出**：每次调 gateway 接口需要用户从 Apifox 复制 curl，Agent 无法自主构造请求（因签名 key 不可知）
2. **数据库表结构需反复人工确认**：写 DDL 脚本或 entity 时，只能靠文档或猜测，缺少真实 schema 校验能力
3. **配置项查看需切换 Nacos 控制台**：排查 SysOption 配置时需要用户开网页查

## 目标

构建一个 **AIP Dev MCP Server**，让 AI Agent 通过 MCP 协议直接访问 AIP 开发环境的三大基础能力：API 调用、数据库查询、配置读取。

---

# 需求分析

## 外部接口及职能

| 接口 | 方向 | 职能 |
|------|------|------|
| AIP Gateway | 调用（MCP → Gateway） | 调用 nsos/onsale/ocs 等业务接口，内置 MD5 签名 |
| MySQL（aip-odm/aip-nsos 等） | 查询（MCP → DB） | 只读查 information_schema，获取表结构、字段定义 |
| Nacos | 读取（MCP → Nacos） | 读配置项当前值，查服务列表 |

## 实现思路

### 架构

```
┌──────────────┐     MCP协议      ┌─────────────────────┐
│  AI Agent    │ ◄──────────────► │  AIP Dev MCP Server │
│  (Hermes/    │   (stdio/HTTP)   │  (Python)           │
│   Claude)    │                  │                     │
└──────────────┘                  │  ┌─────────────────┐│
                                  │  │ gateway_call    ││──► 10.18.8.195:9999
                                  │  │ (API调用+签名)   ││   (AIP Gateway)
                                  │  ├─────────────────┤│
                                  │  │ db_schema       ││──► MySQL (只读)
                                  │  │ (表结构查询)     ││   aip-odm/nsos...
                                  │  ├─────────────────┤│
                                  │  │ nacos_config    ││──► Nacos
                                  │  │ (配置读取)       ││
                                  │  └─────────────────┘│
                                  └─────────────────────┘
```

### 部署位置

- 运行在开发机 WSL 或 Windows 上
- 通过 MCP stdio transport 与 Agent 通信
- 能访问内网 Gateway (10.18.8.195:9999)、MySQL、Nacos

---

# 设计合规

## 异常流程

| 异常场景 | 处理方式 |
|---------|---------|
| Gateway 不可达 | 返回错误 "Gateway unreachable: {url}"，不重试 |
| MySQL 连接超时 | 返回错误 "DB connect timeout: {host}"，5s 超时 |
| Nacos 不可达 | 返回错误 "Nacos unreachable: {url}" |
| 签名 key 未配置 | 启动时校验，缺失则拒绝启动 |
| 非 SELECT 语句 | 校验 SQL 前缀，拒绝非查询语句 |

## 边界值与交互

| 边界项 | 限制 | 处理 |
|-------|------|------|
| SQL 结果行数 | ≤ 500 行 | 超出截断并提示 |
| Gateway 响应体 | ≤ 100KB | 超出截断 |
| 连接超时 | 5s | 超时返回错误 |

## 空值和极端情况

- config.yaml 缺失 → 启动失败，输出缺失项清单
- 数据库连接池耗尽 → 单连接模式，无连接池

## 防御性编程

| 外部依赖 | 策略 |
|---------|------|
| Gateway | 超时 5s，不熔断（低频调用） |
| MySQL | 只读账号，超时 5s，禁止写操作 |
| Nacos | 只读，超时 5s |

## 数据安全和通讯安全

- MySQL 使用只读账号，禁止 INSERT/UPDATE/DELETE/DDL
- 签名 key 存储在本地 config.yaml，不上传版本控制
- MCP stdio transport 本地通信，无网络暴露

## 模块化与解耦

- 三个 tool 各自独立，共享 config 模块
- `gateway_call` 复用 AIP Gateway 的 MD5 签名算法（已逆向）
- `db_schema` 仅调 information_schema，不碰业务数据

## 可扩展性

- 后续可加：`redis_query`（查 Redis 缓存）、`log_search`（查日志）
- Tool 注册通过装饰器，新增 tool 无需改框架代码

---

# 模块设计

## 项目结构

```
aip-dev-mcp/
├── server.py              # MCP Server 入口
├── config.yaml            # 配置文件（不入库）
├── config.yaml.example    # 配置模板
├── tools/
│   ├── __init__.py
│   ├── gateway_call.py    # Gateway API 调用
│   ├── db_schema.py       # 数据库表结构查询
│   └── nacos_config.py    # Nacos 配置读取
├── utils/
│   ├── sign.py            # MD5 签名（逆向 AIP Gateway）
│   └── config.py          # 配置加载
├── requirements.txt
└── README.md
```

## 1. gateway_call

```
Tool: gateway_call
参数:
  - method: str        # 接口方法名，如 "nsos.getCg"
  - biz_content: dict  # 业务参数，如 {"orgCode":"10201","billNo":"..."}
  - app_id: str        # 可选，默认 "951413"
返回:
  - JSON 字符串，Gateway 原始返回
实现:
  1. 从 config 取 md5_key
  2. 组装 params（app_id, charset, format, method, timestamp, version）
  3. 签名：sorted(params)+key → MD5 → base64
  4. POST gateway.action，form-encoded
  5. 返回 JSON
```

## 2. db_schema

```
Tool: db_schema
参数:
  - db: str       # 数据库名，如 "aip-odm", "aip-nsos"
  - table: str    # 表名
  - action: str   # "columns"(默认) | "tables" | "ddl"
返回:
  - columns: 字段名,类型,是否可空,默认值,注释
  - tables: 库中所有表列表
  - ddl: SHOW CREATE TABLE 输出
实现:
  1. 从 config 取对应库的连接信息
  2. pymysql 连接（只读）
  3. 执行 information_schema 查询
  4. 格式化返回
```

## 3. nacos_config

```
Tool: nacos_config
参数:
  - data_id: str    # 如 "aip-nsos.yml"
  - group: str      # 如 "DEFAULT_GROUP"
  - key: str        # 可选，指定配置中的 key（点号分隔路径）
返回:
  - 配置内容 YAML/JSON 字符串，或指定 key 的值
实现:
  1. 从 config 取 Nacos 地址和认证
  2. HTTP GET Nacos Open API
  3. 解析返回
```

## 配置文件

```yaml
# config.yaml（不入库）

gateway:
  url: "http://10.18.8.195:9999/aip-gateway/gateway.action"
  app_id: "951413"
  md5_key: "xxxx"  # 从 gateway Redis 获取

databases:
  aip-odm:
    host: "10.18.8.x"
    port: 3306
    user: "readonly"
    password: "xxxx"
    database: "aip-odm"
  aip-nsos:
    host: "10.18.8.x"
    port: 3306
    user: "readonly"
    password: "xxxx"
    database: "aip-nsos"

nacos:
  url: "http://10.18.8.x:8848"
  username: "nacos"
  password: "xxxx"
```

---

# 测试说明

| 测试模块 | 重点 | 方法 |
|---------|------|------|
| gateway_call | 签名正确性 | 用已知 curl 对比签名值 |
| db_schema | 表结构查询 | 查 plu_info/cnt 表，与已知结构对比 |
| nacos_config | 配置读取 | 读 NSOS_CNT_PLU_CTRL_QUERY_MODE |
| 异常处理 | 连接失败/超时 | 关停目标服务后调用 |

---

# 平滑升级与回滚

## 升级

- Python 依赖通过 requirements.txt 管理
- config.yaml 新增字段向后兼容（新 tool 对应的新配置段）
- MCP 协议版本稳定，server 升级对 Agent 透明

## 回滚

- 回滚到上一版本 server.py + 旧 config.yaml 即可
- 无数据库变更，无持久化状态

## 依赖

```
mcp>=1.0.0
pymysql>=1.1.0
pyyaml>=6.0
requests>=2.31.0
```

---

## 前置条件（开发前需用户提供）

1. Gateway md5Key（从 Redis `aip4:app-access` hash，field `951413-MD5`）
2. MySQL 只读账号（host, port, user, password）
3. Nacos 地址和认证信息
