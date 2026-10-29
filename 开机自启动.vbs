'==========================================================================
' 开机自启动.vbs — 个人工作台开机自启（静默后台服务 + 就绪后自动开浏览器）
'
' 与 启动工作台.bat 的关系：同一套启动逻辑（venv 检查 → 前端构建检查 →
' 后端 uvicorn 8000 → 浏览器），区别是本脚本无交互、后端窗口最小化，
' 适合放入「启动」文件夹随开机自动运行；手动使用仍推荐 启动工作台.bat。
'
' 停止服务：双击 停止服务.bat（按端口 8000 定位进程并校验后结束）。
'
' 自启注册（本仓库已注册一次；如需手动重新注册）：
'   $lnk = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\个人工作台.lnk"
'   $s = (New-Object -COM WScript.Shell).CreateShortcut($lnk)
'   $s.TargetPath = "wscript.exe"
'   $s.Arguments = '"D:\AssetLib\01-财税自动化\personal-workbench\开机自启动.vbs"'
'   $s.WorkingDirectory = "D:\AssetLib\01-财税自动化\personal-workbench"
'   $s.WindowStyle = 7
'   $s.Save()
'==========================================================================
Option Explicit

Dim sh, fso, root
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)

' 0) 服务已在运行（端口已监听）→ 只开浏览器，不重复启动
If PortListening() Then
    sh.Run "http://127.0.0.1:8000"
    WScript.Quit
End If

' 1) venv 不存在 → 回退交互式安装脚本（首次运行需建 venv/装依赖/构建前端）
If Not fso.FileExists(root & "\backend\venv\Scripts\python.exe") Then
    sh.Run """" & root & "\启动工作台.bat""", 1, False
    WScript.Quit
End If

' 2) 前端需要构建时先构建（needs-build.js 退出码非 0 = 需要构建，同启动工作台.bat）
sh.CurrentDirectory = root & "\frontend"
Dim rc
rc = sh.Run("cmd /c node scripts\needs-build.js", 1, True)
If rc <> 0 Then
    sh.Run "cmd /c npm run build", 1, True
End If

' 3) 后台启动后端服务（窗口最小化，不阻塞；日志直接可见，关闭窗口即停止）
sh.CurrentDirectory = root & "\backend"
sh.Run """" & root & "\backend\venv\Scripts\python.exe"" -m uvicorn app.main:app --host 127.0.0.1 --port 8000", 2, False

' 4) 等待端口就绪（最长 60 秒，与启动工作台.bat 相同的探测方式），就绪即开浏览器
Dim i
For i = 1 To 60
    WScript.Sleep 1000
    If PortListening() Then
        sh.Run "http://127.0.0.1:8000"
        WScript.Quit
    End If
Next

' 超时也打开浏览器（服务可能仍在启动中；错误请查看最小化的后端窗口）
sh.Run "http://127.0.0.1:8000"

Function PortListening()
    ' netstat + findstr 无空格正则（与 启动工作台.bat/停止服务.bat 同口径），
    ' 命中 LISTENING 行时 findstr 退出码 0
    Dim r
    r = sh.Run("cmd /c netstat -ano | findstr /r /c:""127.0.0.1:8000 .*LISTENING"" >nul", 0, True)
    PortListening = (r = 0)
End Function
