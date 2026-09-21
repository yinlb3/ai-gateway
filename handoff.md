# 任务交接：ai-gateway

> 计费链路与网关均已跑通，**唯数据库功能未解决**（虚拟 key 因此不可用）。
> 见「已知问题」。

## 当前状态

- 网关已启动并跑通转发与记账，数据库功能关闭
- **六条验证命令全部通过，58/58 项检查**（litellm 1.102.0 下复测）
- 本地与 `gitee/main` 一致（`76f3d64`）；github 侧停在被强推前的旧提交
- 本次改动全部未提交，见文末

## 待办

| # | 事项 | 说明 |
|---|------|------|
| 1 | **数据库连接** | 未定位。可试降到 litellm 1.101.0（该版本曾端到端跑通过） |
| 2 | **虚拟 key 签发与用量统计** | 唯一需要数据库的功能，被 #1 挡住 |
| 3 | github 侧历史中的误入库文件 | 未清理 |
| 4 | 提交本次改动 | — |

## 已知问题：数据库连不上

**litellm 1.102.0 在 Windows 上经 uvicorn 启动时，Prisma 引擎的 HTTP
通信失败。原因未定位。**

已确认的事实：

- `litellm[extra-proxy]` 声明 `prisma>=0.11.0,<1.0`，pip 会装最新版
- prisma **0.15.0** → 启动报 `AttributeError: _Prisma__engine`（issue
  #39114，PR #39117，**均未合并**）
- 降到 **0.11.0** → 崩溃消失，改为 `httpx.ConnectError: All connection
  attempts failed`
- 失败点固定：`_setup_prisma_client` → `health_check()` →
  `query_raw("SELECT 1")`
- **裸 `Prisma()` 连查成功；litellm 的 `PrismaClient` 单独
  `connect()` 也成功；失败只在 uvicorn 的 lifespan 里**
- 换事件循环（`asyncio` / `WindowsSelectorEventLoopPolicy`）**无效**

**别再往 `_Prisma__engine` 那行修**：它在 0.11.0 下不会导致连接失败，
只让引擎包装丢失，issue #39114 解释不了现在的 `ConnectError`。

**绕开办法（已验证）**：`config.yaml` 里**同时**注释掉
`general_settings.database_url` 与 `environment_variables.DATABASE_URL`。
两处都要 —— litellm 在 `proxy_server.py:1170` 用
`get_secret("DATABASE_URL")` 判断是否启用数据库，只看环境变量，不看
`general_settings`，只注释一处无效。`run_gateway.py` 也会主动清掉进程里
的该变量，两道保险。

## 验证结果（2026-09-22）

| 命令 | 结果 |
|------|------|
| `check_pricing.py` | `OK, no error found` |
| `recalc.py` | 三小节齐全，数值与手算一致 |
| `test_callback.py` | 14/14 |
| `test_usage_logger.py` | 20/20 |
| `test_end_to_end.py` | 11/11 |
| `test_multi_instance.py` | 13/13 |

真实网关：

| 项 | 结果 |
|----|------|
| `/health/liveliness` | 200 |
| `/v1/models` | 4 条：2 真名 + 2 别名 |
| **别名** | 退役名解析到真名；**用退役名发请求，记录里的 `model` 一律是真名**（`pricing.yaml` 的退役名条目是防御，非必需） |
| 请求转发 | 200，`usage` 含 `prompt_cache_hit_tokens` |
| **成本重算** | `in=35, out=8` 于谷值时刻算出 `CNY 0.000067`，手算 `35*1+8*4=67` → 一致 |
| **请求头归属** | `x-litellm-spend-logs-metadata` → 记录 `user=wind-forecast` |
| **虚拟 key 归属** | `cline--ai-gateway` → `key=cline--ai-gateway`，`user=ai-gateway` |
| **双进程并发** | 均正确降级到 `_<pid>.jsonl`，不报错退出 |
| **`--merge`** | `Merged 5 rows into 4 unique rows`，按 `req_id` 去重 |
| 失败请求 | 记为 `ok=false` 与 `err`，仍落盘 |

节假日判定（`is_peak`）：

| 时刻 | peak | 说明 |
|------|------|------|
| 2026-09-21 周一 10:00 | True | 普通工作日 |
| 2026-10-01 周四 10:00 | False | 国庆 |
| 2026-10-05 周一 10:00 | False | 假期内，纯星期判断会误算成峰值 |
| 2026-10-08 周四 10:00 | True | 假后上班 |

## 节假日计费

官方排除中国法定节假日，工作日逢节假日整天按谷值算。判定链取第一个有
答案的：

1. **`pricing.yaml` 的 `holidays.dates`** —— 人工核验，**非空即接管整年**
2. **`holidays` 库** —— 仅当该年有完整数据
3. **`chinese_calendar` 库** —— 兜底
4. **都不覆盖** —— 打印 `WARN`，按工作日算，**不终止报表**

两条设计要点：

- **非空即接管整年**：只填几天会让其余假日静默变成工作日。
  `check_pricing.py` 对少于 10 条的年份提示没填全
- **完整性判据**：`holidays` 的 `ChinaStaticHolidays.special_public_holidays`
  表只覆盖已公告年份（当前 2001–2026）。表里没有的年份它仍会答固定日期
  节日，**但农历节日全缺**，春节会被按峰值计费。故视为「不知道」

填充列表：`python tools/fetch_holidays.py 2026`（加 `--dry-run` 只看差异）。
数据源是国务院公告镜像 `NateScarlet/holiday-cn`，其 `papers` 字段指向
公告原文。工具整年替换并写 `verified_at`。

**`recalc.py` 永不联网**，只用磁盘上的列表，无网机器照常重算。

## 本机环境

| 项 | 位置 |
|----|------|
| Postgres 18.6 | **`current` 环境**（非 base）；`pg_ctl` 在 `envs\current\Library\bin\`；数据目录 `envs\current\pgdata`，库 `litellm`，本地 trust 认证 |
| 数据目录 | **需先 `initdb`**，装完包时并不存在 |
| 启动脚本 | `run_gateway.bat` → `run_gateway.py`；登录自启快捷方式在启动文件夹 |
| 网关配置 | `config\config.yaml`，含密钥，已被 .gitignore 排除 |

装法见 `requirements.txt`。三条要点：**LiteLLM 整体走 pip**（conda-forge
的 recipe 只带核心 SDK，无 fastapi/uvicorn/extras）；**prisma 锁 0.11.0**；
**`chinesecalendar` 的导入名是 `chinese_calendar`**。

## 关键决策

- 写日志被占时**回退 pid 文件而非等待或丢弃**：争取短暂分片，一条不丢
- 锁被占时**降级让路而非报错退出**：网关不能因计费锁停摆
- 价格比对**只提示不改价**：价格是人维护的规则
- 静默改写历史时**先建保底标签**；强推**由用户执行**
- 映射表里查不到的模型进 `unknown_models`，**绝不按 0 计费**

## 两种数据结构的区别（易踩坑）

造测试数据前必须分清，曾因此连续三次误判代码有缺陷。

| | LiteLLM payload（输入） | 真实记录（输出） |
|---|---|---|
| 生成方 | 调用方手工构造 | `record.build_record()` |
| 结构 | 嵌套，`metadata` 子字典 | 扁平，全在顶层 |
| 关键字段 | `metadata.user_api_key_alias` | `key`（别名全名）、`user`（项目名） |

记录顶层字段：

```
ts, ok, key, model, in, out, cache_hit, req_id, key_hash8, user,
model_id, api_base, cost_upstream, err, partial, cache_write,
reasoning_tokens
```

`key` 是虚拟 key 别名**全名**（`cline--ai-gateway`），`user` 是按 `--`
切出的**项目名**（`ai-gateway`）。报表 `Cost by key` 显示前者。手写
JSONL 若照 payload 结构写（带嵌套 `metadata`），顶层没有 `key`，报表
显示 `(missing)` —— 这不是缺陷。


## 注意事项

**改模型名时三处必须同步**：`config.yaml` 的 `model_list`、
`pricing.yaml` 的 `model_to_tier`、`test_end_to_end.py` 的测试数据。
漏一处测试会挂在 `unknown_models` 断言上。

**LiteLLM 配置要点**（实测）：

- 别名**只能**写在 `router_settings.model_group_alias`；`model_info` 是
  裸 `dict`，塞 `aliases: [...]` 不报错但**被忽略**
- 同名 `model_name` 重复声明即成为同一池子，请求分摊、失效切换
- `model_name` 是**单值字符串**，不接受列表

**Windows 环境坑**：

- **`ProgramData` 下的 site-packages 需管理员权限才能改**：`Users` 组
  只有 ReadAndExecute，普通 PowerShell 删改报 `WinError 5` 或 `EPERM`
- **tiktoken 词表缓存**：litellm 放在自己的包目录，
  `TIKTOKEN_CACHE_DIR` **对它无效**。下载中断留下的 `.tmp` 与哈希不过的
  正式文件，在 Windows 上 `os.rename` 撞名报 `FileExistsError`。删掉
  正式文件与所有 `.tmp` 即可，需管理员权限
- **PowerShell 5.1 写文件勿用 `Set-Content -Encoding utf8`**：会写 BOM，
  导致 YAML/JSONL 解析失败。改用
  `[IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))`

**Prisma**：

- `prisma` 命令必须在 PATH，LiteLLM 硬调 `subprocess.run(["prisma"])`，
  找不到就跳过建表，随后所有 DB 查询报 502
- 降级后**必须重新生成客户端**：
  `python -m prisma generate --schema <项目根>/schema.prisma`，否则报
  `AttributeError: module 'prisma._types' has no attribute 'SortMode'`
- `schema.prisma` 放**项目根**，路径在生成时固定（`prisma.SCHEMA_PATH`）

**其他**：

- 启动网关须设 `PYTHONUTF8=1`，否则 prisma 生成阶段报 GBK 编码错
- conda 版 Postgres **不开机自启**，重启后手动
  `envs\current\Library\bin\pg_ctl -D envs\current\pgdata start`
- `--merge` 的通配曾过宽（会读进别天文件），改动时留意锚定
- 提交消息不含单引号与反引号，避免 PowerShell 传参被截断
- **测试里的日期要动态构造**：`_sample()` 不带 `ts`，logger 用当前时刻
  命名文件。`test_usage_logger.py` 三处曾写死 `raw_2026-09-21.jsonl`，
  只在 9 月 21 日通过，已改为 `_today()`
- `test_usage_logger.py` 输出的 `WARN partial record written` 与
  `WARN another gateway process owns the raw log` 是测试故意构造的
  场景，**不是缺陷或残留进程**

## 未提交改动

```
check_pricing.py             节假日校验、网关一致性校验
config/config.example.yaml   密钥来源说明、trusted_proxy_ranges
config/pricing.yaml          URL 换中文版、新增 holidays 段
requirements.txt             依赖来源、prisma 版本锁定
src/pricing.py               节假日判定
run_gateway.bat              改为只调 run_gateway.py
test_usage_logger.py         日期改为动态构造
handoff.md                   本文件
run_gateway.py               新增（未跟踪）
schema.prisma                新增（未跟踪，从 litellm 包内复制）
tools/fetch_holidays.py      新增（未跟踪）
```

`config/config.yaml` 已被忽略，不在 git 中。`schema.prisma` 是
`litellm/proxy/schema.prisma` 的副本，上游升级后需重新复制。

