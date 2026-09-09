# 调用 Streamable HTTP 协议 MCP 服务的 PowerShell 脚本
# 用法:
#   .\mcp-call.ps1 -Tool protocol_info
#   .\mcp-call.ps1 -Tool render_course -ArgsJson '{"topic":"名词后缀的语境应用","seconds":8}'
#   .\mcp-call.ps1 -Tool list_course_tasks
# 可选: -Url 指定 MCP 地址 (默认 http://172.16.94.100:8107/mcp)
param(
    [string]$Url = 'http://172.16.94.100:8107/mcp',
    [Parameter(Mandatory = $true)]
    [string]$Tool,
    [string]$ArgsJson = '{}'
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http

function Invoke-Mcp([string]$method, $params, [string]$sessionId) {
    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromSeconds(120)
    try {
        $payload = @{
            jsonrpc = '2.0'
            id      = [guid]::NewGuid().ToString()
            method  = $method
            params  = $params
        } | ConvertTo-Json -Depth 20 -Compress

        $req = New-Object System.Net.Http.HttpRequestMessage
        $req.Method    = [System.Net.Http.HttpMethod]::Post
        $req.RequestUri = $Url
        # Streamable HTTP 必需这两个 Accept，否则会 406
        $req.Headers.TryAddWithoutValidation('Accept', 'application/json, text/event-stream')
        if ($sessionId) {
            $req.Headers.TryAddWithoutValidation('Mcp-Session-Id', $sessionId)
        }
        $req.Content = New-Object System.Net.Http.StringContent($payload, [System.Text.Encoding]::UTF8, 'application/json')

        $resp  = $client.SendAsync($req).Result
        $bytes = $resp.Content.ReadAsByteArrayAsync().Result
        $text  = [System.Text.Encoding]::UTF8.GetString($bytes)   # 强制 UTF-8, 避免 GBK 乱码
        $sid   = $null
        [void]$resp.Headers.TryGetValues('Mcp-Session-Id', [ref]$sid)
        return [pscustomobject]@{
            Status    = [int]$resp.StatusCode
            SessionId = ($sid | Select-Object -First 1)
            Body      = $text
        }
    }
    finally { $client.Dispose() }
}

function Get-SseData([string]$sse) {
    $sse -split "`n" |
        Where-Object { $_ -match '^data:\s*(.+)$' } |
        ForEach-Object { $Matches[1] }
}

# 1) 握手
$init = Invoke-Mcp 'initialize' @{
    protocolVersion = '2025-03-26'
    capabilities    = @{}
    clientInfo      = @{ name = 'mcp-call.ps1'; version = '1.0' }
} $null
$sid = $init.SessionId
if (-not $sid) { throw "initialize 未返回 Mcp-Session-Id" }

# 2) 通知已初始化
$null = Invoke-Mcp 'notifications/initialized' @{} $sid

# 3) 调用工具
$arguments = $ArgsJson | ConvertFrom-Json
if ($null -eq $arguments) { $arguments = @{} }
$call = Invoke-Mcp 'tools/call' @{ name = $Tool; arguments = $arguments } $sid

Write-Host "=== tools/call $Tool (HTTP $($call.Status)) ==="
foreach ($d in (Get-SseData $call.Body)) {
    $obj = $d | ConvertFrom-Json
    if ($obj.result) {
        ($obj.result | ConvertTo-Json -Depth 20)
    }
    elseif ($obj.error) {
        Write-Host "ERROR:" ($obj.error | ConvertTo-Json -Depth 10)
    }
    elseif ($obj.method -eq 'notifications/progress') {
        $p = $obj.params
        Write-Host "[progress] $($p.progress)/$($p.total) - $($p.message)"
    }
}
