@echo off
cd /d "%~dp0"
py -m streamlit run app.py --browser.gatherUsageStats false
pause
