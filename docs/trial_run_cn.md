# 试运行交付说明

> 网关已在本机 4000 端口运行，token 用量实时落盘。
> 本文给使用者最短的接入路径与验证方法。

## 一、网关状态

| 项 | 值 |
|----|----|
| 地址 | `http://127.0.0.1:4000` |
| Admin key | `config/config.yaml` 的 `LITELLM_MASTER_KEY` |
| 数据库 | **未启用**（虚拟 key 不可用，见「已知限制」） |
| 原始记录 | `logs/raw_<日期>.jsonl`，一行一个请求 |

启动方式（双击或后台）：

```bat
D:\Project\ai-gateway\run_gateway.bat
```

## 二、可用模型

| 模型名 | 说明 |
|--------|------|
| `deepseek/deepseek-flash` | 主力，两个上游 key 轮换 |
| `deepseek/deepseek-v4-pro` | 读环境变量 `DEEPSEEK_API_KEY` |
| `deepseek/deepseek-v4-flash` | 退役名别名，解析到 flash |
| `deepseek/deepseek-v4-flash-vision-exp` | 退役名别名，解析到 flash |

用别名发请求，记录里存的是**真名**，价格表无需为别名建条目。

## 三、客户端接入

任何兼容 OpenAI 的客户端，三项即可：

```text
base_url : http://127.0.0.1:4000
api_key  : <LITELLM_MASTER_KEY>
model    : deepseek/deepseek-flash
```

## 四、项目归因（可选，但强烈建议）

报表要区分「哪个项目花的钱」，需在请求里带一个 header：

```text
x-litellm-spend-logs-metadata: {"project":"你的项目名"}
```

**注意**：值是 JSON 字符串，双引号必须原样送达。用命令行工具发送时，
引号极易被 shell 吃掉 —— 实测 PowerShell / curl 会静默丢引号，导致
header 形同不存在。建议用脚本语言构造（见下），或已在 GUI 里配好。

不带这个 header 时：请求**照常成功**，记录标 `user=null` + `partial=true`
+ `err=missing_user`，供事后补配置。缺归属是记账缺口，不是请求失败。

Python 构造示例：

```python
import json, urllib.request

body = json.dumps({
    'model': 'deepseek/deepseek-flash',
    'messages': [{'role': 'user', 'content': 'hello'}],
}).encode()

req = urllib.request.Request(
    'http://127.0.0.1:4000/v1/chat/completions',
    data=body,
    headers={
        'Authorization': 'Bearer <LITELLM_MASTER_KEY>',
        'Content-Type': 'application/json',
        'x-litellm-spend-logs-metadata': json.dumps(
            {'project': 'wind-forecast'}),
    },
)
print(urllib.request.urlopen(req, timeout=60).status)
```

## 五、验证用量被记录

发一次请求，下一秒看原始文件：

```bat
type logs\raw_<今天>.jsonl
```

记录形如：

```json
{"ts": "2026-09-22T02:41:52", "ok": true, "key": "unknown",
 "model": "deepseek/deepseek-flash", "in": 31, "out": 5,
 "cache_hit": 0, "reasoning_tokens": 5, "req_id": "f6c57471-...",
 "key_hash8": "litellm_", "user": "wind-forecast",
 "model_id": "deepseek-flash_Claude+Codex",
 "api_base": "https://api.deepseek.com/chat/completions",
 "cost_upstream": 1.53e-05}
```

关键字段：`in`（输入 token）、`out`（输出 token）、`cache_hit`（命中缓存的
输入）、`reasoning_tokens`（思考 token）。**成本不在记录里**，它是事后算的。

## 六、重算成本

```bat
python recalc.py 2026-09-22
python recalc.py 2026-09-01:2026-09-22
python recalc.py 2026-09-22 --merge
```

报表含三节：货币汇总、按模型、按 key，外加未知模型清单。
**价格改了就重算，历史记录一行不动。**

手算核对（谷值档）：`in=31, out=5` → `31*1 + 5*4 = 51`，除以一百万
= `CNY 0.000051`。

## 七、已知限制

| 限制 | 影响 | 下一步 |
|------|------|--------|
| **虚拟 key 不可用** | 无法用 `<工具>--<项目>` 别名做归因；`/key/generate` 返回 500 | 需启用数据库，见下 |
| `key` 字段恒为 `unknown` | 报表 `Cost by key` 只有一行 | 同上；暂时靠 header 归因 |
| 数据库未启用 | 无虚拟 key、无数据库侧用量统计 | 建表后可解，见 handoff.md |

**数据库为何关闭**：`config/config.yaml` 里 `DATABASE_URL` 与
`general_settings.database_url` **都刻意注释掉**。原因是 litellm 在
数据库路径上启动失败（`prisma migrate diff` 子进程污染 Prisma 引擎，
导致 lifespan 的 health check 撞 `ConnectError`）。根因已定位，见
`handoff.md`。

**关闭数据库不影响本项目的记账**：原始记录写在 `logs/` 下的 JSONL，
重算只读磁盘，全程不碰数据库。

## 八、多实例

同时跑两个网关进程时，`asyncio.Lock` 只在进程内有效，日志会分片到
`raw_<日期>_<pid>.jsonl`。用 `--merge` 合并，按 `req_id` 去重：

```bat
python recalc.py 2026-09-22 --merge
```

以下情形**不是**多实例，无需额外配置：多个项目、多个模型、多个工具
同时在用 —— 它们都走同一个网关进程。
