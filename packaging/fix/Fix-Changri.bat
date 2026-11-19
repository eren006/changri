@echo off
title 长日bot 修复工具
rem 长日bot 打不开时双击这个。它会自己找到长日bot、看看哪里不对、能修的帮你修好。
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1"
