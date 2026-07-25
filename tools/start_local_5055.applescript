property projectPath : "__PROJECT_PATH__"
property serverUrl : "http://127.0.0.1:5055/"
property loginUrl : "http://127.0.0.1:5055/login"

on run
	set runningPid to do shell script "lsof -tiTCP:5055 -sTCP:LISTEN 2>/dev/null || true"
	if runningPid is not "" then
		set runningCwd to do shell script "lsof -a -p " & quoted form of runningPid & " -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1"
		if runningCwd is not projectPath then
			display dialog "5055 当前由其他目录的进程占用：" & runningCwd & "。为避免中断其他服务，BLD 未停止该进程。" buttons {"知道了"} default button 1 with icon caution
			return
		end if

		set runningTTY to do shell script "ps -o tty= -p " & quoted form of runningPid & " | tr -d ' '"
		set shellPid to do shell script "ps -o ppid= -p " & quoted form of runningPid & " | tr -d ' '"
		do shell script "kill -TERM " & quoted form of runningPid
		repeat 40 times
			delay 0.25
			set listenerPid to do shell script "lsof -tiTCP:5055 -sTCP:LISTEN 2>/dev/null || true"
			if listenerPid is "" then exit repeat
		end repeat
		if listenerPid is not "" then
			display dialog "BLD 未能在 10 秒内停止旧的 5055 服务，请检查该服务终端。" buttons {"知道了"} default button 1 with icon caution
			return
		end if

		if runningTTY is not "" then
			set shellTTY to do shell script "ps -o tty= -p " & quoted form of shellPid & " | tr -d ' ' || true"
			if shellTTY is runningTTY then do shell script "kill -HUP " & quoted form of shellPid & " 2>/dev/null || true"
		end if
	end if
	
	set commandPath to "/tmp/bld-start-local-5055.command"
	set commandText to "#!/bin/zsh" & linefeed & "cd " & quoted form of projectPath & " || exit 1" & linefeed & "mkdir -p logs" & linefeed & "echo 'BLD 本机服务启动中，请不要关闭此窗口。'" & linefeed & "echo '访问地址：" & serverUrl & "'" & linefeed & "APP_DEBUG=0 SECRET_KEY=local-dev-bld-matcher .venv/bin/python app.py >> logs/bld-local-5055.log 2>&1" & linefeed & "exit"
	do shell script "printf %s " & quoted form of commandText & " > " & quoted form of commandPath & " && chmod +x " & quoted form of commandPath & " && open -a Terminal " & quoted form of commandPath
	
	repeat 30 times
		delay 0.5
		try
			do shell script "curl -fsS " & quoted form of loginUrl & " >/dev/null"
			open location serverUrl
			display notification "5055 已启动。" with title "BLD 本机服务"
			return
		end try
	end repeat
	
	display dialog "5055 启动命令已发出，但暂时还没有检测到服务响应。请查看 Terminal 窗口或项目里的 logs/bld-local-5055.log。" buttons {"知道了"} default button 1 with icon caution
end run
