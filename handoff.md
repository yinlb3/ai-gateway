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

## 当前状态

- 六项改动全部写完并提交，压成一个本地提交，**未推送**到 gitee / github
- 保底分支 `pre-squash` 保留压缩前的 8 个检查点提交
- **代码一次都没跑过**，只做过 `py_compile` 语法检查

## 待办

1. 跑通基础三条：`python check_pricing.py`、`python recalc.py 2026-09-21`、
   `python test_multi_instance.py`
2. 跑其余三个：`python test_callback.py`、`python test_usage_logger.py`、
   `python test_end_to_end.py`
3. 实测两项核心能力：同时起两个进程写同一日志看是否交错或死锁；
   制造"日志被占用"看记录能否落盘
4. 人工核对：成本相对差 ≤ 0.1%；工作日 10:00 属峰、周末全天属谷；
   未知模型进 `unknown_models` 不得按 0 计费
5. 连真实网关跑 `tools/probe_fields.py`，定下缓存字段键名

## 关键决策

- 写日志被占时**回退 pid 文件而不是等待或丢弃**：争取短暂分片，换取
  一条记录都不丢
- 锁被占时**降级让路而不是报错退出**：网关绝不能因为计费锁而停摆
- 价格比对**只提示不改价**：价格是人维护的规则，程序不得擅改
- 压提交时**保留 `pre-squash` 分支**：可随时回退到压缩前

## 注意事项

- `config/pricing.yaml` 是价格真源，本次未改；代码改动不涉及
  `src/pricing.py`、`src/record.py`、`src/utils.py`
- 提交消息不含单引号与反引号，避免 PowerShell 传参被截断
- `--merge` 的通配曾经过宽（会把别天文件读进来），改动时要留意锚定
- 映射表里查不到的模型必须进 `unknown_models` 清单，不得按 0 计费
- 本项目尚未连过真实网关，缓存字段的键名仍是待确认项
