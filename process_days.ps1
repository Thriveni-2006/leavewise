# Usage (from the code folder):  .\process_days.ps1 -Routes "534,764,..."
# For each chosen day: extract that day -> build stop pass times -> delete the big .db file.
param([Parameter(Mandatory=$true)][string]$Routes)
$Seven = "C:\Program Files\7-Zip\7z.exe"
$Archive = "D:\data\delhi-dtc-bus-gtfs-rt-2026-10.7z"
# 8 weekdays + 2 weekend days (avoids 2 Oct and 20 Oct holidays). Last weekday (14th) becomes the hold-out test day.
$Days = "05","06","07","08","09","10","11","12","13","14"
New-Item -ItemType Directory -Force -Path passes | Out-Null
foreach ($d in $Days) {
    $date = "2026-10-$d"
    if (Test-Path "passes\passes_$date.csv") { Write-Host "skip $date (already done)"; continue }
    $f = "bus_movements_2026_10_$d.db"
    Write-Host "=== $date : extracting ==="
    & $Seven x $Archive $f -oD:\data -y | Out-Null
    if (-not (Test-Path "D:\data\$f")) { Write-Host "!! could not extract $f"; continue }
    Write-Host "=== $date : processing ==="
    python -m leavewise.build_day --db "D:\data\$f" --routes $Routes --data data --out passes
    Remove-Item "D:\data\$f"
}
Write-Host "ALL DONE. Next: python -m leavewise.validate --data data --passes passes"
