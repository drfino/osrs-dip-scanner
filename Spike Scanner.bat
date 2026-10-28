@echo off
cd /d "%~dp0"
start "Spike Scanner (close to stop)" /min python dip_scanner.py serve
