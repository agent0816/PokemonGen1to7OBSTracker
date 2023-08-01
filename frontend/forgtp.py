import subprocess

command = "(Get-NetIPAddress -AddressFamily IPv6 | Where-Object -Property PrefixOrigin -eq \'Dhcp\').IPAddress"
process = subprocess.Popen(["powershell.exe",command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
stdout, stderr = process.communicate()

print(stdout.decode())

