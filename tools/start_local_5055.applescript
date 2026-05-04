property projectPath : "__PROJECT_PATH__"
property serverUrl : "http://127.0.0.1:5055/"
property loginUrl : "http://127.0.0.1:5055/login"

on run
	set runningPid to do shell script "lsof -tiTCP:5055 -sTCP:LISTEN 2>/dev/null || true"
	if runningPid is not "" then
		display notification "5055 已经在运行，正在打开页面。" with title "BLD 本机服务"
		open location serverUrl
		return
	end if
	
	set launchCommand to "cd " & quoted form of projectPath & " && APP_DEBUG=0 SECRET_KEY=local-dev-bld-matcher .venv/bin/python app.py"
	tell application "Terminal"
		activate
		do script launchCommand
	end tell
	
	repeat 30 times
		delay 0.5
		try
			do shell script "curl -fsS " & quoted form of loginUrl & " >/dev/null"
			open location serverUrl
			display notification "5055 已启动。" with title "BLD 本机服务"
			return
		end try
	end repeat
	
	display dialog "5055 启动命令已发出，但暂时还没有检测到服务响应。请看 Terminal 窗口里的错误信息。" buttons {"知道了"} default button 1 with icon caution
end run
