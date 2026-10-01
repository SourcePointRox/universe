# HTCondor 计算节点工作程序包装脚本（Windows 批处理）
# HTCondor submit 文件中 executable 指向此脚本
# %1 = task_id

@echo off
setlocal
set VENV=%~dp0..\..\..\.venv
set PYTHON=%VENV%\Scripts\python.exe
set PROJECT=%~dp0..\..\..

if not exist "%PYTHON%" (
    echo [ERROR] 虚拟环境不存在: %PYTHON%
    exit /b 1
)

cd /d "%PROJECT%"
"%PYTHON%" -m compute.worker %1 --workdir work --results results

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] worker 退出码: %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)
endlocal
