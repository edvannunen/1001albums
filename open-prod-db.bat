@echo off
echo Opening SSH tunnel to prod DB (sqlite-web on the VPS)...
echo Once connected, open http://localhost:8091 in your browser.
echo Press Ctrl+C to close the tunnel.
ssh -L 8091:localhost:8091 root@167.233.148.65
