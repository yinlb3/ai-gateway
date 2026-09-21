@echo off
REM ==========================================================================
REM 在后台启动网关，与终端窗口无关。
REM
REM 密钥写在 config\config.yaml 的 environment_variables 段，
REM 本脚本不含任何凭据。改配置后重跑本脚本即可生效。
REM
REM 双击本文件或执行以下命令启动，网关在后台运行、与终端无关：
REM
REM   Start-Process "D:\Project\ai-gateway\run_gateway.bat" -WindowStyle Hidden
REM ==========================================================================

cd /d D:\Project\ai-gateway

set PYTHONUTF8=1
REM LiteLLM 1.101.0 自带的基线迁移已包含全部列，而随后 166 条增量迁移
REM 又重复加同样的列，全新数据库必然撞 already exists，导致每次启动
REM 都花几分钟逐条 resolve。表已建好，故关掉结构检查。
set DISABLE_SCHEMA_UPDATE=True

if not exist "logs" mkdir "logs"

C:\ProgramData\miniconda3\envs\current\Scripts\litellm.exe --config config\config.yaml --port 4000 >> D:\Project\ai-gateway\logs\gateway.log 2>&1
