param([string]$BaseUrl = '')
$ErrorActionPreference = 'Stop'
try {
    if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
        $BaseUrl = Read-Host 'Paste the Base URL shown by your Model Studio workspace'
    }
    $uri = [Uri]$BaseUrl
    $hostAllowed = $uri.Host -in @(
        'dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com', 'dashscope-us.aliyuncs.com'
    ) -or $uri.Host -match '^[a-zA-Z0-9-]+\.(cn-beijing|ap-southeast-1|ap-northeast-1|eu-central-1|cn-hongkong)\.maas\.aliyuncs\.com$'
    if ($uri.Scheme -ne 'https' -or $uri.Port -ne 443 -or $uri.UserInfo -or
        $uri.Query -or $uri.Fragment -or -not $hostAllowed -or
        $uri.AbsolutePath.TrimEnd('/') -ne '/compatible-mode/v1') {
        throw 'invalid_base_url'
    }
    $secureKey = Read-Host 'DASHSCOPE_API_KEY (masked; never paste into chat)' -AsSecureString
    if ($secureKey.Length -eq 0) { throw 'missing_api_key' }
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    try {
        $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        [Environment]::SetEnvironmentVariable('DASHSCOPE_API_KEY', $plainKey, 'User')
        [Environment]::SetEnvironmentVariable('DASHSCOPE_BASE_URL', $BaseUrl.TrimEnd('/'), 'User')
        [Environment]::SetEnvironmentVariable('WEIRAN_QWEN_MODEL', 'qwen3.8-flash', 'User')
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        $plainKey = $null
        $secureKey.Dispose()
    }
    Write-Host 'Saved to Windows user environment. No API request has been sent.'
    Write-Host 'Restart your server terminal to load the new environment. Live mode stays opt-in.'
} catch {
    Write-Host 'Configuration was not completed. No credential details are printed.'
}
Read-Host 'Press Enter to close' | Out-Null
