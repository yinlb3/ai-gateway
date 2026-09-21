@echo off
REM ==========================================================================
REM Start the gateway in the background, detached from any terminal.
REM
REM All the setup lives in run_gateway.py: the Prisma client reads its URL
REM from the process environment, and variables set here were observed not
REM reaching it. This file only locates the interpreter and the script.
REM
REM Start it by double-clicking, or from PowerShell:
REM
REM   Start-Process "D:\Project\ai-gateway\run_gateway.bat" -WindowStyle Hidden
REM
REM NOTE: keep this file pure ASCII. cmd.exe reads a .bat as ANSI, so a
REM non-ASCII comment turns into mojibake and the words get executed.
REM ==========================================================================

cd /d D:\Project\ai-gateway

if not exist "logs" mkdir "logs"

C:\ProgramData\miniconda3\envs\current\python.exe run_gateway.py --port 4000 >> D:\Project\ai-gateway\logs\gateway.log 2>&1



