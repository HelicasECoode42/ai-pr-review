param(
    [string]$Repo = "owner/repo",
    [int]$PrNumber = 1
)

ai-pr-review analyze $Repo $PrNumber --no-ai --output "reports/pr-$PrNumber.md"
