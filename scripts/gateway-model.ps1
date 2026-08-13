# gateway-model.ps1 — 切换 opencode 模型流量: 经网关(透明代理) <-> 直连
# 用法:
#   .\scripts\gateway-model.ps1 on            opencode 模型走网关(127.0.0.1:8901), 享受整形+路由
#   .\scripts\gateway-model.ps1 on -All       (可选) 注册全部 zen 模型(含其他供应商), 默认排除 free
#   .\scripts\gateway-model.ps1 on -All -IncludeFree  全部模型含 free
#   .\scripts\gateway-model.ps1 off           回退直连: 移除 gateway provider
#   .\scripts\gateway-model.ps1 status        查看当前 opencode.json 是否走网关
#
# 模型清单自动同步: 默认只同步 Go 订阅系列(deepseek/glm/grok/hy3/kimi/mimo/minimax/qwen/gpt-5.6-luna),
#   来源 = `opencode models` 动态拉取(与 opencode 实际可用列表一致, Go 增删模型自动跟随)。
#   其他供应商(claude/gemini/gpt 等)需要时用 -All 启用。
# 注意: 切换后需重启 opencode 或重载配置才生效。
param(
    [Parameter(Position = 0)]
    [ValidateSet("on", "off", "status")]
    [string]$Action = "status",
    [switch]$All,
    [switch]$IncludeFree
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

# ---- Go 订阅系列白名单 (config.yaml zen-go match_models 同源) ----
$GO_SERIES = @('deepseek', 'glm', 'grok', 'hy3', 'kimi', 'mimo', 'minimax', 'qwen', 'gpt-5.6-luna')

# ---- 已知模型显示名/价格 (新模型自动派生, 不影响功能) ----
$GO_NAMES = @{
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
$FREE_NAMES = @{
    "deepseek-v4-flash-free" = "Gateway DeepSeek Free"
    "longcat-2.0-free"       = "Gateway LongCat Free"
    "laguna-s-2.1-free"      = "Gateway Laguna Free"
}

# ---- 从 opencode models 动态拉取模型 ID (与 opencode 实际可用一致) ----
function Get-ZenModelIds {
    try {
        $out = & opencode models 2>$null
        $ids = @($out | ForEach-Object {
            if ($_ -match '^opencode/([^/]+)$') { $matches[1] }
        } | Sort-Object -Unique)
        return $ids
    }
    catch {
        return $null
    }
}

function In-GoSeries([string]$id) {
    foreach ($s in $GO_SERIES) {
        if ($id.StartsWith($s)) { return $true }
    }
    return $false
}

# ---- 构建 gateway 模型清单 ----
function Get-GatewayModels {
    $m = @{}
    $ids = Get-ZenModelIds
    $usedDynamic = $false
    if ($ids -and $ids.Count -gt 0) {
        $usedDynamic = $true
        foreach ($id in $ids) {
            $isFree = $id.EndsWith('-free')
            # 默认只同步 Go 订阅系列; -All 时全部(默认排除 free, -IncludeFree 含 free)
            if (-not $All) {
                if (-not (In-GoSeries $id)) { continue }
            }
            elseif ($isFree -and -not $IncludeFree) {
                continue
            }
            if ($isFree) {
                $name = if ($FREE_NAMES.ContainsKey($id)) { $FREE_NAMES[$id] } else { "Gateway $id" }
                $m[$id] = @{ name = $name; tool_call = $true; cost = @{ input = 0; output = 0 } }
            }
            else {
                if ($GO_NAMES.ContainsKey($id)) {
                    $v = $GO_NAMES[$id]
                    $m[$id] = @{ name = $v[0]; tool_call = $true; cost = @{ input = $v[1]; output = $v[2] } }
                }
                else {
                    $pretty = ($id -replace '[-_]', ' ' -replace '\b\w', { $_.Value.ToUpper() })
                    $m[$id] = @{ name = "Gateway $pretty (Go)"; tool_call = $true; cost = @{ input = 0.25; output = 1 } }
                }
            }
        }
    }
    # fallback: 动态拉取失败时用已知清单 (Go 订阅系列)
    if (-not $usedDynamic) {
        Write-Host "[WARN] opencode models 不可用, 回退已知模型清单" -ForegroundColor Yellow
        foreach ($k in $FREE_NAMES.Keys) {
            $m[$k] = @{ name = $FREE_NAMES[$k]; tool_call = $true; cost = @{ input = 0; output = 0 } }
        }
        foreach ($k in $GO_NAMES.Keys) {
            $v = $GO_NAMES[$k]
            $m[$k] = @{ name = $v[0]; tool_call = $true; cost = @{ input = $v[1]; output = $v[2] } }
        }
    }
    return $m
}

# PS 5.1 已知 bug: ConvertTo-Json 会把单元素数组序列化为标量(如 ["openkilo"] -> "openkilo")。
# 写回前对已知数组字段强制保持数组。
function Repair-ArrayFields {
    foreach ($arrKey in @('plugin', 'instructions', 'disabled_providers', 'enabled_providers')) {
        if ($json.ContainsKey($arrKey) -and $json[$arrKey] -isnot [System.Array]) {
            $json[$arrKey] = @($json[$arrKey])
        }
    }
}

function Set-Gateway {
    $models = Get-GatewayModels
    $json["provider"] = @{
        "gateway" = @{
            "npm"     = "@ai-sdk/openai-compatible"
            "name"    = "AI Gateway (local)"
            "options" = @{
                "baseURL" = "http://127.0.0.1:8901/v1"
                "apiKey"  = "gateway-dev-key"
            }
            "models"  = $models
        }
    }
    Repair-ArrayFields
    $json | ConvertTo-Json -Depth 10 | Set-Content $CfgPath -Encoding UTF8
    $scope = if ($All) { if ($IncludeFree) { "全部模型(含 free)" } else { "全部模型(排除 free)" } } else { "Go 订阅系列" }
    Write-Host "[gateway] enabled: $($models.Count) 个模型注册 ($scope), 经网关 127.0.0.1:8901."
    Write-Host "  Switch models freely in /models; traffic goes through gateway shaping+routing. Restart opencode to apply."
}

function Unset-Gateway {
    if ($json.ContainsKey("provider")) {
        $json.Remove("provider")
    }
    Repair-ArrayFields
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
