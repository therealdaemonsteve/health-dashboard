import Foundation

enum AppConstants {
    static let userId = "YOUR_USER_ID"          // Change to your identifier
    static let apiBaseURL = "YOUR_MCP_LAMBDA_URL" // Set to your MCP Lambda Function URL (from deploy-mcp.sh output)
    static let appleHealthImportPath = "/api/import/apple-health"
    static let batchSize = 500
    static let maxConcurrentUploads = 3
    static let maxRetries = 3
    static let retryBaseDelay: TimeInterval = 2.0
    static let backgroundTaskIdentifier = "com.stevenbennett.healthdashboard.healthsync"
    static let syncAnchorPrefix = "healthkit_anchor_"
    static let aggregatedSyncDatePrefix = "healthkit_aggsync_"
    static let defaultHistoryDays = 730  // 2 years lookback for initial aggregated sync
    static let lastSyncDateKey = "lastHealthSyncDate"
    static let syncLogKey = "healthSyncLogs"
    static let maxSyncLogEntries = 50
}
