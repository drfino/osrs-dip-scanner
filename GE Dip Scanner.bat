@echo off
cd /d "%~dp0"
start "GE Dip Scanner (close to stop)" /min python dip_scanner.py serve
