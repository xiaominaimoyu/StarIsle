@echo off
setlocal
python "%~dp0backup_restore.py" backup %*
