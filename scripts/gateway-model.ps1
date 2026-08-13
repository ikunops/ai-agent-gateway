# gateway-model.ps1 — 切换 opencode 模型流量: 经网关(透明代理) <-> 直连
# 用法:
#   .\scripts\gateway-model.ps1 on     opencode 所有模型走网关(127.0.0.1:8901), 享受整形+路由
#   .\scripts\gateway-model.ps1 off    回退直连: 移除 gateway provider, 恢复你自己配的 provider
#   .\scripts\gateway-model.ps1 status 查看当前 opencode.json 是否走网关
#
# 原理: opencode.json 的 provider.gateway 把 baseURL 指向本地网关。
#   开 = 在 opencode.json 注入 gateway provider(全部模型注册, 模型切换在 /models 自由进行)
#   关 = 从 opencode.json 移除 gateway provider, 恢复原 provider 配置(直连各自上游)
# 注意: 切换后需重启 opencode 或重载配置才生效。
param(
    [Parameter(Position = 0)]
    [ValidateSet("on", "off", "status")]
    [string]$Action = "status"
)

$ErrorActionPreference = "Stop"
$CfgPath = Join-Path $env:USERPROFILE ".config\opencode\opencode.json"

if (-not (Test-Path $CfgPath)) { throw "opencode.json not found: $CfgPath" }

# 读 JSON 为 hashtable (PS 5.1 无 -AsHashtable, 用 ConvertFrom-Json + 递归转换)
function ConvertTo-Hashtable {
    param([Parameter(ValueFromPipeline)]$InputObject)
    process {
        if ($InputObject -is [System.Management.Automation.PSCustomObject]) {
            $h = @{}
            $InputObject.PSObject.Properties | ForEach-Object {
                $h[$_.Name] = ($_.Value | ConvertTo-Hashtable)
            }
            return $h
        }
        elseif ($InputObject -is [System.Collections.IEnumerable] -and $InputObject -isnot [string]) {
            return @($InputObject | ForEach-Object { $_ | ConvertTo-Hashtable })
        }
        else {
            return $InputObject
        }
    }
}

$json = (Get-Content $CfgPath -Raw -Encoding UTF8 | ConvertFrom-Json | ConvertTo-Hashtable)

# ---- gateway provider 模型清单(free + go 27个) ----
function Get-GatewayModels {
    $m = @{}
    $free = @{
        "deepseek-v4-flash-free" = "Gateway DeepSeek Free"
        "longcat-2.0-free"       = "Gateway LongCat Free"
        "laguna-s-2.1-free"      = "Gateway Laguna Free"
    }
    foreach ($k in $free.Keys) {
        $m[$k] = @{ name = $free[$k]; tool_call = $true; cost = @{ input = 0; output = 0 } }
    }
    $go = @{
        "deepseek-v4-flash" = @("Gateway DeepSeek V4 Flash (Go)", 0.25, 1)
        "deepseek-v4-pro"   = @("Gateway DeepSeek V4 Pro (Go)", 2.5, 10)
        "glm-5"             = @("Gateway GLM-5 (Go)", 2, 8)
        "glm-5.1"           = @("Gateway GLM-5.1 (Go)", 2, 8)
        "glm-5.2"           = @("Gateway GLM-5.2 (Go)", 2, 8)
        "gpt-5.6-luna"      = @("Gateway GPT 5.6 Luna (Go)", 2, 10)
        "grok-4.5"          = @("Gateway Grok 4.5 (Go)", 0.5, 2)
        "hy3"               = @("Gateway Hy3 (Go)", 0.5, 2)
        "hy3-preview"       = @("Gateway Hy3 Preview (Go)", 0.5, 2)
        "kimi-k2.5"         = @("Gateway Kimi K2.5 (Go)", 0.5, 2)
        "kimi-k2.6"         = @("Gateway Kimi K2.6 (Go)", 0.5, 2)
        "kimi-k2.7-code"    = @("Gateway Kimi K2.7 Code (Go)", 0.5, 2)
        "kimi-k3"           = @("Gateway Kimi K3 (Go)", 0.5, 2)
        "mimo-v2.5"         = @("Gateway MiMo V2.5 (Go)", 0.3, 0.5)
        "mimo-v2.5-pro"     = @("Gateway MiMo V2.5 Pro (Go)", 1, 3)
        "mimo-v2-omni"      = @("Gateway MiMo V2 Omni (Go)", 1, 3)
        "mimo-v2-pro"       = @("Gateway MiMo V2 Pro (Go)", 1, 3)
        "minimax-m2.5"      = @("Gateway MiniMax M2.5 (Go)", 1, 3)
        "minimax-m2.7"      = @("Gateway MiniMax M2.7 (Go)", 1, 3)
        "minimax-m3"        = @("Gateway MiniMax M3 (Go)", 1, 3)
        "qwen3.5-plus"      = @("Gateway Qwen3.5 Plus (Go)", 1, 3)
        "qwen3.6-plus"      = @("Gateway Qwen3.6 Plus (Go)", 1, 3)
        "qwen3.7-max"       = @("Gateway Qwen3.7 Max (Go)", 1, 3)
        "qwen3.7-plus"      = @("Gateway Qwen3.7 Plus (Go)", 1, 3)
        "qwen3.8-max"       = @("Gateway Qwen3.8 Max (Go)", 1, 3)
    }
    foreach ($k in $go.Keys) {
        $v = $go[$k]
        $m[$k] = @{ name = $v[0]; tool_call = $true; cost = @{ input = $v[1]; output = $v[2] } }
    }
    return $m
}

function Set-Gateway {
    $json["provider"] = @{
        "gateway" = @{
            "npm"     = "@ai-sdk/openai-compatible"
            "name"    = "AI Gateway (local)"
            "options" = @{
                "baseURL" = "http://127.0.0.1:8901/v1"
                "apiKey"  = "gateway-dev-key"
            }
            "models"  = Get-GatewayModels
        }
    }
    $json | ConvertTo-Json -Depth 10 | Set-Content $CfgPath -Encoding UTF8
    Write-Host "[gateway] enabled: all opencode models routed via gateway (127.0.0.1:8901)."
    Write-Host "  Switch models freely in /models; traffic goes through gateway shaping+routing. Restart opencode to apply."
}

function Unset-Gateway {
    if ($json.ContainsKey("provider")) {
        $json.Remove("provider")
    }
    $json | ConvertTo-Json -Depth 10 | Set-Content $CfgPath -Encoding UTF8
    Write-Host "[gateway] fallback to direct: gateway provider removed."
    Write-Host "  Your models now connect directly to their upstreams. Restart opencode to apply."
    Write-Host "  Note: any other provider you configured in opencode.json is also removed by this; restore manually if needed."
}

function Show-GatewayStatus {
    $prov = $json["provider"]
    if ($prov -and $prov["gateway"]) {
        $models = ($prov["gateway"]["models"].Keys | Measure-Object).Count
        Write-Host "[gateway] opencode.json is routed via gateway: provider.gateway present, $models models registered"
    }
    else {
        Write-Host "[gateway] opencode.json is in direct mode (no provider.gateway)"
    }
}

switch ($Action) {
    "on"     { Set-Gateway }
    "off"    { Unset-Gateway }
    "status" { Show-GatewayStatus }
}
