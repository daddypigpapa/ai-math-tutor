# 관리자 PowerShell 에서 1회 실행: 개인(Private) 네트워크에서만 8000번 허용. 공용 네트워크·인터넷에는 열지 않는다.
New-NetFirewallRule -DisplayName "AI 가정교사 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
Write-Host "아이 PC에서 http://<서버 PC IP>:8000 으로 접속한다. 서버 IP 확인: ipconfig"
