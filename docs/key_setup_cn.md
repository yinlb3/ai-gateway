# 虚拟 Key 与项目归因设置

> 本文说明如何签发 LiteLLM 虚拟 Key，让每条记录带上"工具"与"项目"两个维度。

## 为什么需要虚拟 Key

报表要回答"哪个工具、在哪个项目上花了多少钱"。这两件事都来自请求本身，但
LiteLLM 不会自动知道，需要人为提供：

| 维度 | 来源 | 职责 |
|------|------|------|
| 工具 | 虚拟 Key 别名 | 区分 Cline / Claude Code / Codex |
| 项目 | header 或别名拆分 | 区分 wind-forecast / ai-gateway |

两者是**独立维度**，不能互相替代。一个工具跑多个项目时，同名 Key 分不出项目。

## 方案一：Key 别名拆分（主路径）

适合只能配置 `base_url` 与 `api_key` 的 CLI 工具（Claude Code、Codex）。

**别名格式**：`<工具>--<项目>`，用两个连字符分隔。

| 别名 | 拆分出的项目 |
|------|-------------|
| `codex--wind-forecast` | `wind-forecast` |
| `cline--ai-gateway` | `ai-gateway` |
| `claude--paper-visibility` | `paper-visibility` |
| `codex` | 无（记录为 `user=null`） |

**命名规则**：

- 只用小写字母与连字符，禁用中文与空格
- 一个项目一个 Key，N 个项目就 N 个 Key
- **项目名本身不得含 `--`**，拆分只认第一个分隔符

**签发方式**（需要 Postgres 与 master key）：

```bat
curl -X POST http://localhost:4000/key/generate ^
  -H "Authorization: Bearer <MASTER_KEY>" ^
  -H "Content-Type: application/json" ^
  -d "{\"key_alias\": \"codex--wind-forecast\"}"
```

返回的 `key` 就是配到工具里的 api_key。

**待实测**：`key_alias` 是否接受 `--` 与所需长度。若被拒，改用 `_` 或 `:`
作分隔符，并同步修改 `src/record.py` 的 `_split_alias()`。

## 方案二：请求 header

适合能自定义 header 的图形界面工具（Cline）。

```
x-litellm-spend-logs-metadata: {"project":"wind-forecast"}
```

值是 **JSON 字符串**，引号与花括号必须原样送达。callback 读的是
`metadata["spend_logs_metadata"]["project"]`。

**待实测**：Cline 的"自定义 Header"输入框能否承载这样的值。若不能，
退化为方案一。

## 解析顺序

callback 按以下顺序取值，取到即止：

1. `metadata["spend_logs_metadata"]["project"]` —— header 方案
2. `metadata["user_api_key_alias"]` 按 `--` 拆分 —— 别名方案
3. 都失败 → 记 `user=null` + `partial=true` + `err=missing_user`

**第 3 种情形下请求照常成功**。缺项目归属是记账的缺口，不是请求的失败，
所以记录里标出来供人工补配置，而不阻断业务。

## 记录里存了什么

| 字段 | 内容 | 说明 |
|------|------|------|
| `key` | Key **别名** | 如 `codex--wind-forecast` |
| `key_hash8` | Key 哈希前 8 位 | 别名可被改名，哈希不可，作稳定标识 |
| `user` | 项目名 | 从 header 或别名拆出 |

**安全约束**：只记别名与哈希前 8 位，**绝不记录 key 本身**。哈希不可逆，
既能稳定标识又不泄露凭据。

## 启用数据库

虚拟 Key 存储在数据库里，因此需要 Postgres：

```yaml
general_settings:
  database_url: os.environ/DATABASE_URL
  master_key: os.environ/LITELLM_MASTER_KEY
```

`DATABASE_URL` 形如
`postgresql://user:password@host:5432/litellm`，真实值放环境变量，
不写进 `config.yaml`。

**注意**：本项目自己的记账**不依赖数据库**——原始记录写在 `logs/` 下的
JSONL 文件里。数据库只为签发虚拟 Key 而需要。

## 多实例情形

若同时运行两个 LiteLLM 进程，`asyncio.Lock` 只在进程内有效，日志可能交错。
此时用 `recalc.py --merge` 合并，它按 `req_id` 去重。

以下情形**不是**多实例，无需任何额外配置：

- 同时开多个项目或工作区
- 同时使用多个模型
- 同时有多个工具在请求

它们都走同一个网关进程、同一个事件循环，一把锁足够。
