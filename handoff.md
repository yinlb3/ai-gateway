# 任务交接：ai-gateway

> ## ⛔ 项目已暂停（2026-09-22）
>
> **不设恢复日期。** 暂停原因不是技术故障,而是**产品定位未成立**：
> 该产品的核心价值（按工具/项目归因）**要求客户端接入网关**,而这
> 被判定为成本高于收益。详见「项目暂停记录」。
>
> **代码本身是完整的**：转发、记账、重算、58 项检查全部实测通过。
> 若有人愿意接入网关,本文件足以让接手者继续。
>
> **恢复前请先读「项目暂停记录」一节**,不要直接跳到实现细节。

> 计费链路与网关均已跑通，**唯数据库功能未解决**（虚拟 key 因此不可用）。
> 根因**已定位**，见「已知问题」。
>
> 试运行的接入说明见 `docs/trial_run_cn.md`。

## 当前状态

- **项目状态：暂停。** 代码可用，但未产生实际使用价值
- 网关曾在 4000 端口运行，转发与记账已实测跑通
- **六条验证命令全部通过，58/58 项检查**（litellm 1.102.0 下复测）
- 本地 = `gitee/main` = `github/main`，三处一致（`44024bd`，工作区干净）
- 数据库功能关闭，虚拟 key 不可用

## 待办

> 全部待办已随项目暂停冻结。恢复时重新评估优先级。

| # | 事项 | 说明 |
|---|------|------|
| 1 | **数据库连接** | 根因已定位：空库 + `DISABLE_SCHEMA_UPDATE` 跳过建表。见下 |
| 2 | **虚拟 key 签发与用量统计** | 唯一需要数据库的功能，被 #1 挡住 |
| 3 | **产品方向重评估** | **最高优先级**。见「项目暂停记录」 |
| 4 | ~~github 侧历史中的误入库文件~~ | **已核实不存在**。见「误入库」一节 |
| 5 | ~~提交本次改动~~ | **已完成**（`44024bd`） |

## 项目暂停记录（2026-09-22）

> 这一节说明**为什么暂停**,供将来重启时参考。

### 暂停结论

**实现完整,但产品定位未成立。**

该产品的核心价值是「按工具、按项目归因花费」,而这**必须**让请求经过网关。
让使用者为此改变既有工作方式,成本高于收益。

### 产品定位（当初为什么做）

设计文档 `design_cn.md` 第 1 节的目标：

> - 支持按工具（Cline / Claude Code / Codex）、**按项目（工作区）维度归因**

这个能力 **AI 厂商官方账单给不了**：官方只看得到一个 API key,
不知道请求来自哪个工具、花在哪个项目上。

**因此「必须经网关」不是实现缺陷,而是这个产品的定义** ——
不经过中介,就无法知道请求的来源。

### 中断点：客户端接不进来

**实测事实（2026-09-22）**：

| 项 | 结果 |
|----|------|
| 网关自身 | ✅ 正常。请求 1 秒内落盘,token 精确匹配,成本重算与手算一致 |
| 用脚本发请求 | ✅ 正常。`user` 归因成功（header 方式） |
| **用 Cline 发请求** | ❌ **接不进来** |

**根因（Cline 配置确认）**：

用户所用的 Cline 配置为内置 `DeepSeek` provider，
其配置界面**只有 `API Key` 与 `Model`,没有 `Base URL` 字段**。
即：想接网关也无处填写地址。

改用 `OpenAI Compatible` provider 可以接入,但代价包括：

1. 需要改动客户端配置
2. 全部流量依赖网关存活（单点故障）
3. 网关上游 key 的限流会影响日常使用

**综合三项代价,接入的收益不足以覆盖成本。**

### 关键结论（接手者必读）

| # | 结论 |
|---|------|
| 1 | **先验证客户端能否接入,再写实现。** 设计文档第 11 节「待定项 3、4」（Cline header 与 key 别名）是承重墙,却直到最后才验证 |
| 2 | **归因需求必然要求中介。** 这是产品定义级的代价,应在动工前确认是否接受 |
| 3 | **"工具可用"不等于"用户会用"。** 检查全绿不代表产品成立,最后一公里（客户端接入）不通则整体无价值 |

### 已排除的方向（避免重走）

| 判断 | 实情 |
|------|------|
| "网关拉起位置不对" | 与位置无关。网关监听 `0.0.0.0:4000` 正常,原因是客户端未指向它 |
| "直连也能解决,不必接网关" | 不成立。直连只能拿到官方已有的总量/按模型数字,**做不出工具与项目归因** |
| "只需改客户端 provider 即可" | 技术上成立,但产品上代价过高（见「中断点」） |

### 若将来重启,先回答这三个问题

1. **谁是这个产品的用户？**
   - 团队/同事（可要求接入）→ 现有形态成立
   - 仅作者自己（不愿改习惯）→ 需重新设计产品形态

2. **若无工具/项目归因,本产品还有价值吗？**
   - 若"只要总量与按模型" → 官方账单已足够,本项目属过度设计
   - 若"必须有归因" → 必须先解决客户端接入

3. **是否接受"透明拦截"形态？**
   - 即本地代理 + 本地 CA,客户端零配置
   - 代价：安装证书、处理各客户端证书校验、维护成本较高
   - **属于换产品形态,不是修 bug**

### 可复用资产

以下成果独立成立,可用于任何 OpenAI 兼容客户端：

| 资产 | 状态 |
|------|------|
| 两层分离设计（事实层 + 规则层） | ✅ 完整落地,改价不动历史 |
| 节假日感知的峰谷计费 | ✅ 国庆、调休判定正确 |
| 多实例并发与 `--merge` 去重 | ✅ 已实现并测试 |
| 58 项自动检查 | ✅ 全绿,改动有保障 |
| 数据库问题根因定位 | ✅ 结论明确,见上文 |
| 记账与报表链路 | ✅ 任何 OpenAI 兼容客户端均可接入 |

### 恢复时的第一步

```bat
python check_pricing.py
python test_callback.py
python test_usage_logger.py
python test_end_to_end.py
python test_multi_instance.py
python recalc.py 2026-09-22
```

预期：58/58 全绿（无需数据库）。若网关需要重启：

```bat
run_gateway.bat
```

**注意**：开机自启**未配置**（启动文件夹内没有网关快捷方式）。

## 已知问题：数据库连不上（根因已定位 2026-09-22）

**症状**：开启 `DATABASE_URL` 经 uvicorn 启动，lifespan 里的
`health_check()` 抛 `httpx.ConnectError: All connection attempts failed`，
`Application startup failed. Exiting.`。

**根因不是 Prisma 引擎，也不是版本问题，而是「空库 + 建表被跳过」：**

1. `litellm` 库存在但**是空的**，一张表都没有（`psql -d litellm -c "\dt"`
   报 `Did not find any tables.`）
2. `run_gateway.py` 的 `DEFAULT_ENV` 设了 `DISABLE_SCHEMA_UPDATE=True`，
   而 `proxy_cli.py:1341` 用
   `should_update_prisma_schema(general_settings.get("disable_prisma_schema_update"))`
   判断：**环境变量为 True 时不执行建表**
3. 于是库始终为空。启动时 `proxy_cli.py:1342` 的
   `check_prisma_schema_diff()` 跑 `prisma migrate diff` 子进程，报
   `prisma schema out of sync with db` 并列出 160+ 条建表 SQL
4. 该子进程自带一个独立的 Prisma query engine，用完即弃，**污染了随后
   lifespan 里 `PrismaClient.connect()` / `health_check()` 的 engine 状态**，
   导致 HTTP 层连不上

**关键反证（实测）**：单独构造 client 完全正常 ——

```
A) connect OK
B) health_check -> [{'?column?': 1}]
C) is_connected -> True
D) engine type -> _TrackedPrismaEngine
E) query_raw -> [{'?column?': 1}]
```

失败**只在 uvicorn lifespan 里**出现，因为只有那条路会先跑
`prisma migrate diff`。这与「Prisma 0.11.0 有 bug」的旧判断不同。

**`DISABLE_SCHEMA_UPDATE=True` 的原始意图是对的**（针对已有库，避免 166 条
增量迁移反复报 already exists），但库为空时它把**初始化一起关掉了**。

**旧判断中已作废的部分**：

- ~~`_Prisma__engine`（issue #39114）导致连接失败~~ —— 无关，且 0.11.0 下
  不复发。原来的警告仍然成立：别往那行修
- ~~降到 1.101.0 可解~~ —— 未验证，且与根因无关
- ~~降级 0.11.0 后「崩溃消失，改为 ConnectError」~~ —— 这是**两个独立问题**
  的先后暴露，不是同一个 bug 的两种表现

**正确的修法（未执行）**：对空库先跑一次建表，让 schema 与 db 同步 ——

```bat
prisma db push --accept-data-loss --skip-generate
```

`DATABASE_URL` 指向 `postgresql://postgres@localhost:5432/litellm`，需在
`schema.prisma` 所在目录（项目根）执行。见「Prisma」一节。

**绕开办法（当前采用，已验证）**：`config.yaml` 里**同时**注释掉
`general_settings.database_url` 与 `environment_variables.DATABASE_URL`。
两处都要 —— litellm 在 `proxy_server.py` 用 `get_secret("DATABASE_URL")`
判断是否启用数据库，只看环境变量，不看 `general_settings`，只注释一处无效。
`run_gateway.py` 也会主动清掉进程里的该变量，两道保险。

**关闭数据库不影响本项目的记账**：原始记录写在 `logs/` 的 JSONL，重算
只读磁盘。虚拟 key 是唯一受影响的功能。

## 误入库文件（已核实：不存在）

旧待办第 3 条担心「github 侧历史中的误入库文件」，2026-09-22 核实**不存在**：

- `git ls-tree HEAD` 与 `gitee/main` 均无 `tmp_*`、`conda*` 之类文件
- 全历史搜 master key 字面量、`config/config.yaml`、`config/.env`、
  `logs/*`：**零命中**
- `tmp_holiday_probe.py`、`tmp_log.txt`、`tmp_loop.py`、`tmp_p6.py`、
  `tmp_out.txt`、`conda修复记录*.md` 确实**曾被 add**，但只活在 Cline
  自己的检查点 ref（`refs/cline/checkpoints/*`，334 个提交）与已被 `reset`
  抛弃的 reflog 里，**从未进入 `main`**

如需彻底清掉这些本地 ref（不影响 `main`，纯减小体积）：

```bat
git for-each-ref --format="%(refname)" refs/cline/ | ForEach-Object {
    git update-ref -d $_ }
```

**执行前须确认没有进行中的 Cline 会话依赖它们。**

## 验证结果（2026-09-22）

| 命令 | 结果 |
|------|------|
| `check_pricing.py` | `OK, no error found` |
| `recalc.py` | 三小节齐全，数值与手算一致 |
| `test_callback.py` | 14/14 |
| `test_usage_logger.py` | 20/20 |
| `test_end_to_end.py` | 11/11 |
| `test_multi_instance.py` | 13/13 |

**试运行链路实测（2026-09-22 02:41，单进程单文件，无分片）**：

| 项 | 结果 |
|----|------|
| 网关 | 4000 端口，`/health/liveliness` → 200 |
| `/v1/models` | 4 条：2 真名 + 2 别名 |
| **实时落盘** | 请求返回 `02:41:18`，记录写入 `02:41:19`，`req_id` 与响应一致 |
| **token 精确匹配** | 响应 `prompt_tokens:37 / completion_tokens:20` → 记录 `in:37 / out:20 / reasoning_tokens:20` |
| **header 归因** | `x-litellm-spend-logs-metadata: {"project":"wind-forecast"}` → 记录 `user:"wind-forecast"`，`partial` 字段消失 |
| **成本重算** | 4 行合计 CNY 0.000378，手算 `117+105+105+51=378` → 一致 |
| 未知模型清单 | `(empty)`，4 行全部正确计价 |

**归因实测的重要提醒**：`x-litellm-spend-logs-metadata` 的值是 **JSON 字符串**，
双引号必须原样送达。用 PowerShell / curl 从命令行发送时，**引号会被 shell
静默吃掉**，header 形同不存在，记录退化为 `user=null`。此坑在本次实测中
真实踩到并误判为「项目缺陷」三次。**必须用脚本语言构造请求**（见
`docs/trial_run_cn.md` 的 Python 示例），或使用已在 GUI 配好 header 的客户端。

真实网关（历史记录，仍有效）：

| 项 | 结果 |
|----|------|
| `/health/liveliness` | 200 |
| `/v1/models` | 4 条：2 真名 + 2 别名 |
| **别名** | 退役名解析到真名；**用退役名发请求，记录里的 `model` 一律是真名**（`pricing.yaml` 的退役名条目是防御，非必需） |
| 请求转发 | 200，`usage` 含 `prompt_cache_hit_tokens` |
| **成本重算** | `in=35, out=8` 于谷值时刻算出 `CNY 0.000067`，手算 `35*1+8*4=67` → 一致 |
| **虚拟 key 归属** | `cline--ai-gateway` → `key=cline--ai-gateway`，`user=ai-gateway`（需数据库，当前不可用） |
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
| Postgres 18.6 | **`current` 环境**（非 base）；`pg_ctl` 在 `envs\current\Library\bin\`；数据目录 `envs\current\pgdata`，库 `litellm`（**空库**），本地 trust 认证 |
| 数据目录 | **需先 `initdb`**，装完包时并不存在 |
| 启动脚本 | `run_gateway.bat` → `run_gateway.py`；登录自启快捷方式在启动文件夹 |
| 网关配置 | `config\config.yaml`，含密钥，已被 .gitignore 排除 |
| 当前运行 | 网关在 4000 端口（`run_gateway.bat` 后台启动） |

装法见 `requirements.txt`。三条要点：**LiteLLM 整体走 pip**（conda-forge
的 recipe 只带核心 SDK，无 fastapi/uvicorn/extras）；**prisma 锁 0.11.0**；
**`chinesecalendar` 的导入名是 `chinese_calendar`**。

## logs/ 目录约定（2026-09-22 整理）

`logs/` 只放**当天的原始记录**与网关日志，历史调试文件一律归档：

```
logs/
  raw_2026-09-22.jsonl          当天原始记录
  gateway.log                   网关日志（run_gateway.bat 追加写）
  .lock                         进程锁（含 pid 与心跳）
  archive/                      归档：测试数据与历史诊断日志
    raw_*.testdata.jsonl        测试期构造的记录
    lock.stale-pid*.json        已死进程留下的陈旧锁
    diagnostics/                gw1..gw9 / loop / gwrun 等调试输出
```

**排障提示**：网关起不来时先看 `logs/gateway.log` 头部，它会打印
`DATABASE_URL = ...` 与启动阶段的关键行。此前一次失败的首行就是
`Changes to DB Schema detected`，直接指向根因。

**陈旧 `.lock` 会导致文件分片**：`.lock` 指向的 pid 若已死，新进程会误判
「有别的进程占着」，降级写 `raw_<日期>_<pid>.jsonl`。清掉陈旧 `.lock`
即可恢复单文件；已分片的用 `recalc.py --merge` 合并。

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
handoff.md                   本文件（2026-09-22 更新根因与验证结论）
run_gateway.py               新增（未跟踪）
schema.prisma                新增（未跟踪，从 litellm 包内复制）
tools/fetch_holidays.py      新增（未跟踪）
docs/trial_run_cn.md         新增（未跟踪，试运行交付说明）
```

`config/config.yaml` 已被忽略，不在 git 中。`schema.prisma` 是
`litellm/proxy/schema.prisma` 的副本，上游升级后需重新复制。

**`logs/` 已在 2026-09-22 整理**：历史调试文件与测试数据移入
`logs/archive/`，当日原始记录从 `raw_2026-09-22.jsonl` 重新开始。
`logs/` 本身被 .gitignore 排除，归档不影响版本库。

