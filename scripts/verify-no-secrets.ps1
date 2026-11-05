$ErrorActionPreference = 'Stop'
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$failures = New-Object System.Collections.Generic.List[string]

Push-Location $ProjectRoot
try {
  $relativeFiles = @(git ls-files --cached --others --exclude-standard)
  if ($LASTEXITCODE -ne 0) {
    throw 'Unable to enumerate git-tracked and unignored files.'
  }
} finally {
  Pop-Location
}

$files = $relativeFiles |
  Where-Object { $_ -and $_ -notmatch '^\.tmp/' } |
  ForEach-Object { Get-Item -LiteralPath (Join-Path $ProjectRoot $_) -Force -ErrorAction SilentlyContinue } |
  Where-Object { $null -ne $_ -and -not $_.PSIsContainer }

foreach ($file in $files) {
  $rootText = $ProjectRoot.Path.TrimEnd('\') + '\'
  $rel = $file.FullName
  if ($rel.StartsWith($rootText, [System.StringComparison]::OrdinalIgnoreCase)) {
    $rel = $rel.Substring($rootText.Length)
  }
  if ($file.Name -ne '.env.example' -and ($file.Name -match '^\.env(\.|$)' -or $file.Name -eq 'auth.json')) {
    $failures.Add("Forbidden file: $rel")
    continue
  }
  if ($rel -match '(^|\\)\.claude\\settings\.local\.json$') {
    $failures.Add("Forbidden file: $rel")
    continue
  }
  if ($file.Length -gt 5MB) { continue }
  $text = Get-Content -Raw -Encoding UTF8 -ErrorAction SilentlyContinue $file.FullName
  if ($null -eq $text) { continue }

  $ipPattern = '\b(?:\d{1,3}\.){3}\d{1,3}\b'
  $patterns = @(
    '-----BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----',
    '(?m)^\s*TELEGRAM_BOT_TOKEN\s*=\s*(?!\*{3}masked\*{3}|"?\*{3}redacted\*{3}|replace-with-|example|changeme)\S+',
    '\b\d{8,12}:[A-Za-z0-9_-]{30,}\b',
    $ipPattern,
    '(?i)\b(openai|anthropic|github|todoist|notion|api)[_-]?(key|token)\s*[:=]\s*(?!["'']?\*{3}|replace-with-|example|changeme)[A-Za-z0-9_\-\.]{16,}',
    '(?i)\b(password|passwd|pwd)\s*[:=]\s*(?!["'']?\*{3}|example|changeme|null|false)[^\s]{6,}'
  )
  foreach ($pattern in $patterns) {
    if ($pattern -eq $ipPattern) {
      $publicIps = [regex]::Matches($text, $ipPattern) |
        Where-Object {
          $_.Value -notmatch '^(127\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|169\.254\.|0\.0\.0\.0$|255\.255\.255\.255$|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.)'
        }
      if (@($publicIps).Count -gt 0) {
        $failures.Add("Potential secret in $rel matching $pattern")
        break
      }
      continue
    }
    if ($text -cmatch $pattern) {
      $failures.Add("Potential secret in $rel matching $pattern")
      break
    }
  }
}

if ($failures.Count -gt 0) {
  $failures | ForEach-Object { Write-Error $_ }
  exit 1
}

Write-Host 'No obvious secrets found.'
