@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 微北洋全站抓取

:menu
cls
echo ============================================================
echo   微北洋「青年湖底」全站抓取
echo ============================================================
echo.
if exist "data\sitewide.db" (
  for /f "delims=" %%i in ('python crawl.py status --brief 2^>nul') do echo   %%i
  echo.
)
echo ------------------------------------------------------------
echo   [1] 全部爬取    把全站帖子抓到底（第一次用这个）
echo   [2] 继续爬取    接着上次没抓完的地方继续
echo   [3] 部分爬取    指定板块 / 日期 / Mp号，只抓这些
echo   [4] 补充爬取    只抓上次之后的新帖子
echo.
echo   [5] 打开看板    启动数据服务并打开浏览器（推荐）
echo   [6] 账号设置    查看当前账号 / 清除已保存的密码
echo   [7] 重新排队    把「已完成」的板块打开，继续往更旧的帖子抓
echo   [8] 重建看板    数据更新后重新生成页面
echo   [0] 退出
echo ------------------------------------------------------------
echo.
set "CL="
set /p "CL=请输入序号后回车："

if "%CL%"=="1" goto full
if "%CL%"=="2" goto resume
if "%CL%"=="3" goto partial
if "%CL%"=="4" goto update
if "%CL%"=="5" goto serveboard
if "%CL%"=="6" goto account
if "%CL%"=="7" goto reopen
if "%CL%"=="8" goto rebuild
if "%CL%"=="0" goto quit
goto menu

:account
cls
echo ============================================================
echo   账号设置
echo ============================================================
echo.
python crawl.py whoami
echo.
echo ------------------------------------------------------------
echo   [1] 清除已保存的密码（下次运行重新输入）
echo   [2] 返回
echo ------------------------------------------------------------
echo.
set "AC="
set /p "AC=请输入序号后回车："
if "%AC%"=="1" (
  python crawl.py logout
  echo.
  pause
)
goto menu

:needcred
if not "%WPY_ACCOUNT%"=="" if not "%WPY_PASSWORD%"=="" exit /b 0
if exist "data\凭据.json" exit /b 0
rem 既没有环境变量也没有凭据文件：让 Python 自己交互式询问并保存（密码不回显）
exit /b 0

:full
call :needcred
if errorlevel 1 goto menu
echo.
echo 开始全部爬取。全站数据量大，想先小规模试可以按 Ctrl+C 停下，
echo 改用 [3] 部分爬取（填个条数上限，几十秒就能看到结果）。
echo.
echo 3 秒后自动开始，不用按键...
timeout /t 3 /nobreak >nul
python crawl.py all
goto after

:resume
call :needcred
if errorlevel 1 goto menu
echo.
python crawl.py resume
goto after

:partial
call :needcred
if errorlevel 1 goto menu
echo.
echo ============================================================
echo   部分爬取
echo ============================================================
echo.
echo   [1] 选择分区（可选多个）
echo   [2] 全部 8 个分区
echo.
set "SEL="
set /p "SEL=请选择 (回车=全部)：" 
if "%SEL%"=="1" goto pickboard
set "TY="
goto pickrange

:pickboard
echo.
echo   1 校务专区    2 青年湖底    3 学习新知    4 交友恋爱
echo   5 二次元      6 摆摊       10 Bugs反馈   13 美食专区
echo.
set "TY="
set /p "TY=输入分区编号，多个用逗号隔开 (如 2,3)：" 
if "%TY%"=="" goto pickboard

:pickrange
echo.
echo ============================================================
echo   抓取范围（二选一）
echo ============================================================
echo.
echo   [1] 按帖子数量（开发测试用，如 100）
echo   [2] 按日期（某一天或起止区间）
echo   [3] 不限范围（抓到底）
echo.
set "RG="
set /p "RG=请选择 (回车=不限)："

set "N="
set "DT="
set "D2="
if "%RG%"=="1" goto askcount
if "%RG%"=="2" goto askdate
goto pickready

:askcount
set /p "N=抓多少条 例如 100："
goto pickready

:askdate
set /p "DT=起始日期 例如 2026-09-01："
set /p "D2=结束日期 直接回车表示到今天："

:pickready

set "ARGS="
if not "%TY%"=="" set "ARGS=!ARGS! --types %TY%"
if not "%N%"==""  set "ARGS=!ARGS! --limit %N%"
if not "%DT%"=="" set "ARGS=!ARGS! --since-date %DT%"
set "ARGS=!ARGS! --no-confirm"

echo.
echo 即将执行：
echo    python crawl.py partial !ARGS!
echo.
echo 注：抓取目标是「补充库中还没抓过的正文与评论」，
echo     已抓过的不会重复抓，所以范围只决定处理哪些板块。
echo.
set "GO="
set /p "GO=确认执行？(y=执行，其他=返回)："
if /i not "%GO%"=="y" goto menu
echo.
python crawl.py partial !ARGS!
goto after

:update
call :needcred
if errorlevel 1 goto menu
echo.
echo 开始补充爬取，只取库里最新帖子之后的新帖。
echo 3 秒后自动开始，不用按键...
timeout /t 3 /nobreak >nul
python crawl.py update
goto after

:reopen
echo.
echo 说明：早期版本因为服务端给的页数不准，每个板块只抓了 20 页就
echo       标记成「已完成」。这个选项把这些板块重新打开，让你能继续
echo       往更旧的帖子抓。已抓到的帖子不会丢。
echo.
python crawl.py reopen
goto done

:serveboard
if not exist "data\全站看板.html" (
  echo.
  echo 还没有生成看板，请先执行 [1] 或 [2] 抓取。
  pause
  goto menu
)
echo.
echo ============================================================
echo   打开看板
echo ============================================================
echo.
echo   会启动一个本地数据服务并自动打开浏览器。
echo.
echo   为什么需要服务：全站 20 万帖 + 评论，
echo   全部塞进单个 HTML 会超过 100 MB，浏览器打不开。
echo   改成服务后数据随查随取，翻页、搜索、筛选都快。
echo.
echo   注意：
echo   - 不要直接双击 data\全站看板.html（没有数据接口，是空的）
echo   - 不要用浏览器打开 全站帖子_raw.json / CSV
echo     （168MB / 64MB，Chrome 可能吃掉数 GB 内存）
echo.
echo   附带好处：导出帖子为图片时会自动带上图片。
echo   用完后回到这个窗口按 Ctrl+C 停止服务。
echo.
pause
python crawl.py serve
goto menu

:rebuild
if not exist "data\sitewide.db" (
  echo.
  echo 还没有数据，请先执行 [1] 或 [2] 抓取。
  pause
  goto menu
)
echo.
python crawl.py build
echo.
echo 重建完成。用 [5] 打开看板查看。
pause
goto menu

:after
if exist "data\sitewide.db" (
  echo.
  echo 数据已写入 data\sitewide.db。
  echo.
  echo 不要直接双击 data\全站看板.html 或用浏览器打开 raw.json/CSV：
  echo 看板必须通过菜单 [5]「打开看板」启动本地服务后再看，
  echo 否则要么是空页面，要么大文件会把浏览器内存吃爆。
  echo.
)
echo ------------------------------------------------------------
echo 结束。进度存在 data\sitewide.db，下次运行会接着抓。
echo ------------------------------------------------------------
pause
goto menu

:quit
endlocal
exit /b 0
