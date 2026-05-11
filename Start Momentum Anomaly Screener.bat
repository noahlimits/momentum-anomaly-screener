@echo off
cd /d "%~dp0"
set STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
set STREAMLIT_SERVER_SHOW_EMAIL_PROMPT=false
py -3.13 -m streamlit run dashboard.py --server.port=8561 --server.address=localhost --server.showEmailPrompt=false --browser.gatherUsageStats=false
pause
