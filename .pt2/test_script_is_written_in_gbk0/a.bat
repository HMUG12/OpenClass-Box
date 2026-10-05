@echo off
echo START>"E:\新创意构思\OpenClass\.pt2\test_script_is_written_in_gbk0\a.result"
netsh advfirewall firewall delete rule name="OpenClass-Box 局域网服务" >nul 2>&1
netsh advfirewall firewall delete rule name="OpenClass-Box 局域网发现" >nul 2>&1
netsh advfirewall firewall add rule name="OpenClass-Box 局域网服务" dir=in action=allow protocol=TCP localport=38610,38620,38900 profile=private,domain >>"E:\新创意构思\OpenClass\.pt2\test_script_is_written_in_gbk0\a.result" 2>&1
netsh advfirewall firewall add rule name="OpenClass-Box 局域网发现" dir=in action=allow protocol=UDP localport=38901 profile=private,domain >>"E:\新创意构思\OpenClass\.pt2\test_script_is_written_in_gbk0\a.result" 2>&1
echo DONE>>"E:\新创意构思\OpenClass\.pt2\test_script_is_written_in_gbk0\a.result"
