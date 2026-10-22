$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Start-Compose {
    docker compose -f compose.yaml up -d --build
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose 启动失败，请检查上方错误信息。"
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "没有找到 Docker。请先安装并启动 Docker Desktop，然后重新运行本脚本。"
}

docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "当前 Docker 未启用 Compose。请更新 Docker Desktop 后重试。"
}

$envPath = Join-Path $PSScriptRoot ".env"
if (Test-Path $envPath) {
    $answer = Read-Host "检测到已有 .env。直接使用现有配置启动？(Y/n)"
    if ($answer -notmatch '^(n|no)$') {
        Start-Compose
        Write-Host "`n部署完成： http://127.0.0.1:8080"
        $usernameMatch = Select-String -LiteralPath $envPath -Pattern '^DASHBOARD_USERNAME=(.*)$' | Select-Object -First 1
        $passwordMatch = Select-String -LiteralPath $envPath -Pattern '^DASHBOARD_PASSWORD=(.*)$' | Select-Object -First 1
        $dashboardUsername = if ($usernameMatch) { $usernameMatch.Matches.Groups[1].Value } else { $null }
        $dashboardPassword = if ($passwordMatch) { $passwordMatch.Matches.Groups[1].Value } else { $null }
        if ($dashboardUsername -and $dashboardPassword) {
            Write-Host "仪表盘用户名：$dashboardUsername"
            Write-Host "仪表盘密码：$dashboardPassword"
        }
        else {
            Write-Host "请查看 .env 中的 DASHBOARD_USERNAME 和 DASHBOARD_PASSWORD。"
        }
        exit 0
    }
}

Write-Host "首次部署只需准备 Telegram Bot Token 和你的 Telegram 数字用户 ID。"
$botToken = Read-Host "Bot Token（在 @BotFather 创建机器人后获得）"
if ([string]::IsNullOrWhiteSpace($botToken) -or $botToken -match '[\r\n]') {
    throw "Bot Token 不能为空，也不能包含换行。"
}

$adminIds = Read-Host "管理员 Telegram 数字用户 ID（多个 ID 用逗号分隔）"
if ($adminIds -notmatch '^\s*\d+(\s*,\s*\d+)*\s*$') {
    throw "管理员 ID 格式不正确，请输入数字 ID；多个 ID 用逗号分隔。"
}
$adminIds = ($adminIds -split ',' | ForEach-Object { $_.Trim() }) -join ','

$randomBytes = New-Object byte[] 24
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $rng.GetBytes($randomBytes)
}
finally {
    $rng.Dispose()
}
$dashboardPassword = -join ($randomBytes | ForEach-Object { $_.ToString('x2') })

$envContent = @"
BOT_TOKEN=$botToken
ADMIN_IDS=$adminIds
API_ID=
API_HASH=
USER_SESSION_ENABLED=0
LOG_LEVEL=INFO
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=$dashboardPassword
DASHBOARD_URL=
DASHBOARD_BIND=127.0.0.1
DASHBOARD_PORT=8080
CLOUDFLARE_TUNNEL_TOKEN=
"@
Set-Content -LiteralPath $envPath -Value $envContent -Encoding ascii

Start-Compose
Write-Host ""
Write-Host "部署完成： http://127.0.0.1:8080"
Write-Host "仪表盘用户名：admin"
Write-Host "仪表盘密码：$dashboardPassword"
Write-Host "登录信息已保存在项目目录的 .env 文件中，请妥善保管。"
