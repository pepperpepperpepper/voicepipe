# Setup OpenAI API Key as environment variable

Write-Host "=== Voicepipe API Key Setup ===" -ForegroundColor Cyan
Write-Host ""

# Check if already set
$currentKey = [System.Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "User")
if ($currentKey) {
    Write-Host "OPENAI_API_KEY is already set in user environment." -ForegroundColor Green
    Write-Host "Current value: $($currentKey.Substring(0,7))..." -ForegroundColor Gray
    $response = Read-Host "Do you want to update it? (y/n)"
    if ($response -ne 'y') {
        Write-Host "Keeping existing API key." -ForegroundColor Yellow
        exit
    }
}

# Prompt for API key
Write-Host ""
Write-Host "Please enter your OpenAI API key:" -ForegroundColor Yellow
Write-Host "(Get it from https://platform.openai.com/api-keys)" -ForegroundColor Gray
$apiKey = Read-Host -AsSecureString "API Key"

# Convert secure string to plain text
$BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($apiKey)
$plainKey = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)

# Validate key format
if ($plainKey -notmatch '^sk-[a-zA-Z0-9]{48}$') {
    Write-Host "Warning: API key doesn't match expected format (sk-...)" -ForegroundColor Yellow
    $confirm = Read-Host "Continue anyway? (y/n)"
    if ($confirm -ne 'y') {
        Write-Host "Setup cancelled." -ForegroundColor Red
        exit
    }
}

# Set as user environment variable
try {
    [System.Environment]::SetEnvironmentVariable("OPENAI_API_KEY", $plainKey, "User")
    Write-Host ""
    Write-Host "Success! OPENAI_API_KEY has been set as a user environment variable." -ForegroundColor Green
    Write-Host ""
    Write-Host "Note: You may need to:" -ForegroundColor Yellow
    Write-Host "1. Restart any open terminals or applications" -ForegroundColor White
    Write-Host "2. Log out and back in for all applications to see the change" -ForegroundColor White
    Write-Host ""
    Write-Host "The Voicepipe daemon will now use this API key automatically." -ForegroundColor Green
} catch {
    Write-Host "Error setting environment variable: $_" -ForegroundColor Red
}

# Clean up
[System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR)