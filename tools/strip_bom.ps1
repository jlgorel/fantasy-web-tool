$root = 'c:\Users\jlgor\Documents\fantasy-web-tool\frontend'
$found = @()
Get-ChildItem -Path $root -Recurse -File | ForEach-Object {
    $bytes = [System.IO.File]::ReadAllBytes($_.FullName)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        $found += $_.FullName
        $stripped = New-Object byte[] ($bytes.Length - 3)
        [System.Array]::Copy($bytes, 3, $stripped, 0, $bytes.Length - 3)
        [System.IO.File]::WriteAllBytes($_.FullName, $stripped)
    }
}
Write-Host "Stripped BOM from $($found.Count) files:"
$found | ForEach-Object { Write-Host "  $_" }
