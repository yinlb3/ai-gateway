# ai-gateway

> **⛔ 项目已暂停（2026-09-22）。**
>
> 技术实现完整可用（58 项检查全绿），但**产品定位未成立**：
> 核心能力「按工具/项目归因」必须让客户端接入网关，
> 该成本被判定为高于收益。
>
> **重启前请先读 `handoff.md` 的「项目暂停记录」一节。**

通过 LiteLLM 网关，按工具与项目统计 AI Token 花费。

## 目录

- [安装](#安装)
- [使用方式](#使用方式)
- [工作原理](#工作原理)
- [里程碑](#里程碑)

## 安装

```bat
pip install -r requirements.txt
```

需要 Python 3.12 或更高版本。计费侧依赖为 `pyyaml`（读取价目表）与
`arrow`（时间处理）。

只有跑网关的那台机器还需要装 LiteLLM：

```bat
pip install "litellm[proxy,extra-proxy]"
```

两半刻意分开：callback 与全部报表模块只依赖原始记录，因此报表可以在
从不承载网关的机器上重算。这组 extras 请用 pip 装，conda 装会缺包——
它拉取的一部分包在 conda-forge 上没有发布。

## 使用方式

工具链分两半：网关 callback 在请求进行时落事实，报表事后把事实换成钱。

### 1. 让网关挂上 callback

把 `config/config.example.yaml` 复制为 `config.yaml`，填好模型名与存放 API Key 的
环境变量，然后启动 LiteLLM。加上这一行，每个完成的请求都会到达 callback：

```yaml
litellm_settings:
  callbacks: custom_callback.proxy_handler_instance
```

请求的项目归属有两个来源：`x-litellm-spend-logs-metadata` header，或虚拟 Key 别名
（写成 `<工具>--<项目>`）。完整规则见 `docs/key_setup_cn.md`。

### 2. 重算报表

```bat
python recalc.py 2026-09-21
```

指定日期区间，或合并同一天的多份进程文件：

```bat
python recalc.py 2026-09-01:2026-09-21
python recalc.py 2026-09-21 --merge
```

### 3. 改过价格后校验价目表

```bat
python check_pricing.py
```

### 4. 跑测试

```bat
python test_callback.py
python test_usage_logger.py
python test_end_to_end.py
```

## 工作原理

两层刻意分开存放。

| 层 | 存什么 | 生命周期 | 文件 |
|----|--------|---------|------|
| 事实层 | 时间、模型、token 数 | 只追加 | `logs/raw_YYYY-MM-DD.jsonl` |
| 规则层 | 单价、币种、高峰时段 | 人工维护 | `config/pricing.yaml` |

成本由两层推导得出，**从不写进记录**。因此改一次价格就能修正全部历史账，
一行日志都不用动。

DeepSeek 的高峰时段为北京时间周一至周五 9:00–12:00、14:00–18:00，
其余时间（含周末）为空闲时段。

模型名通过 `model_to_tier` 映射到档位，一个价格档可覆盖该模型的旧名。
映射表里查不到的模型进入 `unknown_models` 且不记成本，**绝不按 0 计算**。

报表按币种分开输出，不做汇率折算、不跨币种合计。

**callback 绝不打断请求**：每个入口都自己捕获异常，模块导入期不做任何事，
缺项目归属只记为缺口而不抛错。

## 里程碑

> 项目已于 2026-09-22 暂停。下表为暂停时的状态。

| 项目 | 状态 | 说明 |
|------|------|------|
| 价目表 | 已完成 | DeepSeek 两档，含高峰与空闲价 |
| 价目表校验器 | 已完成 | 字段、时段、缓存、超期检查 |
| 成本报表 | 已完成 | 按币种输出，含未知模型清单 |
| 网关 callback | 已完成 | 每请求一行，失败请求同样记录 |
| 真实字段核对 | 已完成 | 缓存字段键名已实测确认 |
| **客户端接入** | **未打通** | **项目暂停原因**，见 `handoff.md` |

- **作者**: yinlb<yinlb3@foxmail.com>, deepseek-flash

Last Updated: 2026-09-22

[English](README.md)

