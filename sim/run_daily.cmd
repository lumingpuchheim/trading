@echo off
cd /d C:\Users\user\workspace\trading
echo ---- %DATE% %TIME% daily ---- >> sim\jobs.log
"C:\Users\user\AppData\Local\Programs\Python\Python313\python.exe" -m sim.jobs daily >> sim\jobs.log 2>&1
