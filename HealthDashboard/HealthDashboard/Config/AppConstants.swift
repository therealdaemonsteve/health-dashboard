import Foundation

enum AppConstants {
    static let userId: String = Bundle.main.infoDictionary?["HDUserId"] as? String ?? ""
    static let apiBaseURL: String = Bundle.main.infoDictionary?["HDApiBaseURL"] as? String ?? ""
    static let backgroundTaskIdentifier: String = Bundle.main.infoDictionary?["HDBgTaskId"] as? String ?? ""
    static let bundleIdentifier: String = Bundle.main.bundleIdentifier ?? "com.example.healthdashboard"

    // Non-user-configurable constants
    static let appleHealthImportPath = "/api/import/apple-health"
    static let batchSize = 500
    static let maxRetries = 3
    static let retryBaseDelay: TimeInterval = 2.0
    static let syncAnchorPrefix = "healthkit_anchor_"
    static let aggregatedSyncDatePrefix = "healthkit_aggsync_"
    static let defaultHistoryDays = 730
    static let lastSyncDateKey = "lastHealthSyncDate"
    static let syncLogKey = "healthSyncLogs"
    static let maxSyncLogEntries = 50
}
