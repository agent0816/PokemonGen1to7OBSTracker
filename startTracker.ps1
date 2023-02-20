$python = python --version
$python

if($python -ne "Python 3.10.10"){
    if((Test-Path -Path .\tmp) -eq $False){
        New-Item -Path ".\" -Name "tmp" -ItemType "directory"
    }
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.10.10/python-3.10.10-amd64.exe" -OutFile ".\tmp\python-3.10.10-amd64.exe"
    Start-Process -FilePath ".\tmp\python-3.10.10-amd64.exe" -Wait
}

python -m pip install --upgrade pip
$yaml = $true
$kivy = $true

$installed = (python -m pip list).split("`n")
for($i = 0; $i -lt $installed.Length; $i++){
    $package = $installed[$i].split(" ")[0]
    if($yaml -eq $true){
        $yaml = -not ($package -eq "PyYAML")
    }
    if($kivy -eq $true){
        $kivy = -not ($package -eq "Kivy")
    }
}

