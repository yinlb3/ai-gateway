# 任务交接：ai-gateway

## 已完成

- 多进程安全：新增 `src/lockfile.py`，跨进程互斥 + 心跳检测，后启动的
  实例发现锁被占时降级让路，不报错退出
- 记录不丢：`custom_callback.py` 写主文件失败时回退 pid 文件，之后由
  `recalc.py --merge` 合并回来
- 合并更准：`recalc.py` 的 `--merge` 通配锚定目标日期，不再串日期，
  同时把 pid 文件纳入读取
- 价格可核：新增 `src/compare.py`，与本项目价目对照 LiteLLM 内置价目，
  列出差异；`check_pricing.py` 加了入口
- 启动自检：新增 `start.bat`、`start.sh`，依赖缺失即报出
- 新用户上手：新增 `config/pricing.demo.yaml` 示例价目表，不参与计费
- 配套测试：新增 `test_multi_instance.py`；扩充 `test_usage_logger.py`、
  `test_end_to_end.py`
- 文档：`design_cn.md` 记下峰值判定改为按记录时间戳推导

## 本机网关环境（2026-09-21 搭建）

首次把网关在本机跑通，前置条件全部落地：

| 项 | 位置 |
|----|------|
| Postgres 18.6 | conda base，数据目录 `C:\ProgramData\miniconda3\pgdata`，库名 `litellm` |
| prisma 客户端 | 装在用户目录 `%APPDATA%\Python\Python312\site-packages`（conda 那份不完整） |
| `prisma.exe` | 复制到 `%APPDATA%\npm`（该目录本就在 PATH，LiteLLM 硬调 `prisma` 命令） |
| 启动脚本 | `run_gateway.bat`，登录自启快捷方式已放入启动文件夹 |
| 网关配置 | `config\config.yaml`，含密钥，已被 .gitignore 排除 |

## 当前状态

- 六项改动已提交并推送到 gitee（`da4414c`），本地与 `origin/main` 一致
- 误入库的 `conda_base_修复记录.md` 已用 `git filter-repo` 从全库历史清除，
  并强推覆盖 gitee；github 侧因网络不通未处理
- **六条验证命令全部跑通，共 58/58 项检查通过**
- **真实网关端到端跑通一次**（探针模式）：记录落盘、成本与手算零误差
- 另有一批改动未提交，见文末「未提交改动」

## 待办

1. **填真实密钥后启动网关**：`config\config.yaml` 的
   `environment_variables.DEEPSEEK_API_KEY` 目前是占位值
2. **实测 `model_group_alias`**：别名写法从源码推出，尚未跑通验证
3. **签发虚拟 Key**：网关起来后调 `POST /key/generate`，别名格式
   `<工具>--<项目>`（`docs/key_setup_cn.md:47` 的待验证项）
4. **双进程真实并发追加的肉眼确认**：锁竞争已由单测覆盖，真实观感未看
5. github 侧历史中的误入库文件未清理

## 验证结果（2026-09-21）

| 命令 | 结果 |
|------|------|
| `check_pricing.py` | `OK, no error found`，退出 0 |
| `recalc.py 2026-09-21` | 三小节齐全，数值精确 |
| `test_multi_instance.py` | 13/13 |
| `test_callback.py` | 14/14 |
| `test_usage_logger.py` | 20/20 |
| `test_end_to_end.py` | 11/11 |

CLI 层实测：

- 峰值档 `in=12000, cache_hit=10000, out=3000` 算出 `0.0284 CNY`，
  与手工式完全一致（相对差 0%）
- 谷值档 pro 模型 `in=50000, cache_hit=40000, out=20000` 算出
  `0.321 CNY`，亦完全一致
- `--merge` 读主文件 + pid 文件，`Merged 2 rows into 2 unique rows`
- `Cost by key` 按虚拟 key 别名全名分组，子项求和等于货币总计

真实网关实测（探针模式，`tools/probe_fields.py`）：

- 缓存键名确认为 **`prompt_cache_hit_tokens`**，位置在
  **`metadata.usage_object`**（`hidden_params.usage_object` 为 null，
  `record.py:95-97` 的回退分支命中）
- `model` 字段值与 `pricing.yaml` 的 `model_to_tier` 键完全一致
- 回调收到的 kwargs 含 `standard_logging_object`，与 `_slo_from` 一致

## 关键决策

- 写日志被占时**回退 pid 文件而不是等待或丢弃**：争取短暂分片，换取
  一条记录都不丢
- 锁被占时**降级让路而不是报错退出**：网关绝不能因为计费锁而停摆
- 价格比对**只提示不改价**：价格是人维护的规则，程序不得擅改
- 静默改写历史时**先建保底标签再动手**：确认无误后再删
- 强推**由用户执行**：破坏性操作不代跑，只把命令备好

## LiteLLM 配置要点（实测确认）

**别名只能写在 `router_settings.model_group_alias`**：

```yaml
router_settings:
  model_group_alias:
    "旧名字": "model_list 里已声明的真名"
```

`model_info` 类型是 `dict`，塞 `aliases: [...]` 不报错但**被忽略**，
启动日志不会列出该名字（已实测）。

**同一模型配多把钥匙**：`environment_variables` 声明多个变量，
`model_list` 里为每把钥匙复制一条部署，`model_name` 写相同。同名即
同一池子，请求自动分摊、单把失效自动切换。**框架没有部署级 aliases
字段**（`DeploymentTypedDict` 只有 `model_name`/`litellm_params`/
`model_info` 三项）。

**`model_name` 是单值字符串**，不接受列表。

## 两种数据结构的区别（易踩坑）

造测试数据前必须分清，本轮曾因此连续三次误判代码有缺陷：

| | LiteLLM payload（输入） | 真实记录（输出） |
|---|---|---|
| 生成方 | 调用方手工构造 | `record.build_record()` |
| 结构 | 嵌套，`metadata` 子字典 | 扁平，全部在顶层 |
| 关键字段 | `metadata.user_api_key_alias` | `key`（别名全名）、`user`（项目名） |

真实记录的顶层字段：

```
ts, ok, key, model, in, out, cache_hit, req_id, key_hash8, user,
model_id, api_base, cost_upstream, err, partial, cache_write,
reasoning_tokens
```

注意 `key` 是虚拟 key 别名**全名**（如 `cline--ai-gateway`），`user` 才是
按 `--` 切出的**项目名**（如 `ai-gateway`）。报表 `Cost by key` 显示前者。
手写 JSONL 时若照 payload 结构写（带嵌套 `metadata`），顶层没有 `key`，
报表会显示 `(missing)` —— 这不是缺陷。

## 注意事项

- `config/pricing.yaml` 是价格真源，本次只删了 2 行注释后已还原
- 修改能调用的模型名时，**三处必须同步**：`config.yaml` 的 model_list、
  `pricing.yaml` 的 model_to_tier、`test_end_to_end.py` 的测试数据。
  漏一处会导致测试挂在 `unknown_models` 断言上
- 提交消息不含单引号与反引号，避免 PowerShell 传参被截断
- `--merge` 的通配曾经过宽（会把别天文件读进来），改动时要留意锚定
- 映射表里查不到的模型必须进 `unknown_models` 清单，不得按 0 计费
- **PowerShell 5.1 写文件勿用 `Set-Content -Encoding utf8`**：会写入 BOM，
  导致 YAML/JSONL 解析失败。改用
  `[IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))`
- **LiteLLM 1.101.0 的迁移文件自相冲突**：`0_init` 基线已含全部列，
  166 条增量迁移又重复加同一列，全新库必然撞 `already exists`，每次
  启动都花几分钟 resolve。以 `DISABLE_SCHEMA_UPDATE=True` 跳过
- **`prisma` 命令必须在 PATH**：LiteLLM 硬调 `subprocess.run(["prisma"])`，
  找不到就误判为未安装并跳过建表，随后所有 DB 查询报 502
- 启动网关须设 `PYTHONUTF8=1`，否则 prisma 生成阶段报 GBK 编码错
- conda 版 Postgres **不开机自启**，重启后需手动
  `pg_ctl -D C:\ProgramData\miniconda3\pgdata start`
- `test_usage_logger.py` 输出的 `WARN partial record written` 与
  `WARN another gateway process owns the raw log (pid N)` 均为预期噪音，
  是测试故意构造的场景，不是残留进程或缺陷

## 未提交改动

```
.gitignore                 config/config.yaml 忽略规则等
README.md / README_cn.md   网关依赖说明
config/config.example.yaml 多厂商/多 Key 示例
requirements.txt           litellm[proxy,extra-proxy] 说明
handoff.md                 本文件
run_gateway.bat            新增（未跟踪）
```

`config/config.yaml` 已被忽略，不在 git 中。

