property restartScriptPath : "__RESTART_SCRIPT_PATH__"

on run
	do shell script quoted form of restartScriptPath
end run
