# Script to trigger the Option Alpha webhook endpoint
$url = "http://127.0.0.1:5000/option_alpha_trigger"

try {
    Write-Host "Calling webhook at: $url" -ForegroundColor Cyan
    $response = Invoke-WebRequest -Uri $url -Method POST -ErrorAction Stop
    
    Write-Host "Status Code: $($response.StatusCode)" -ForegroundColor Green
    Write-Host "Response: $($response.Content)" -ForegroundColor Green
    
    # Log to file (optional)
    $logPath = "C:\Users\ChrisRen\Desktop\Option Trading\GPT4_TradeIndicator\webhook_log.txt"
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logPath -Value "$timestamp - Status: $($response.StatusCode) - Response: $($response.Content)"
}
catch {
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    
    # Log error to file
    $logPath = "C:\Users\ChrisRen\Desktop\Option Trading\GPT4_TradeIndicator\webhook_log.txt"
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logPath -Value "$timestamp - ERROR: $($_.Exception.Message)"
}