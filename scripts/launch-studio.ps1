# Lance Talaxie Studio sur le workspace du dépôt (talend-workspace/), avec PATH et JAVA_HOME relus
# depuis le registre : indispensable juste après l'installation du JDK, tant que la session n'a pas été rouverte.
param([string]$Studio = 'C:\Talaxie\8.9.0\TOS_DI.exe')
$repo = Split-Path -Parent $PSScriptRoot
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
$env:JAVA_HOME = [Environment]::GetEnvironmentVariable('JAVA_HOME', 'Machine')
Start-Process -FilePath $Studio -ArgumentList '-data', (Join-Path $repo 'talend-workspace') -WorkingDirectory (Split-Path $Studio)
