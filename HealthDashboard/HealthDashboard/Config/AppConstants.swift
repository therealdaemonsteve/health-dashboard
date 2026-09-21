import Foundation

enum AppConstants {
    private static let apiBaseURLKey = "HD_ApiBaseURL"

    // Reads from UserDefaults first (set in-app), falls back to Info.plist (set at build time)
    static var apiBaseURL: String {
        if let stored = UserDefaults.standard.string(forKey: apiBaseURLKey), !stored.isEmpty {
            return stored
        }
        return Bundle.main.infoDictionary?["HDApiBaseURL"] as? String ?? ""
    }

    // User ID from Info.plist — only used by developer builds via xcconfig
    static let userId: String = Bundle.main.infoDictionary?["HDUserId"] as? String ?? ""

    static var isConfigured: Bool {
        !apiBaseURL.isEmpty
    }

    static func configure(apiBaseURL: String) {
        UserDefaults.standard.set(apiBaseURL, forKey: apiBaseURLKey)
    }

    static func clearConfiguration() {
        UserDefaults.standard.removeObject(forKey: apiBaseURLKey)
    }

    static let backgroundTaskIdentifier: String = Bundle.main.infoDictionary?["HDBgTaskId"] as? String ?? ""
    static let bundleIdentifier: String = Bundle.main.bundleIdentifier ?? "com.example.healthdashboard"

    // Non-user-configurable constants
    static let appleHealthImportPath = "/api/import/apple-health"
    static let batchSize = 5000
    static let maxRetries = 3
    static let retryBaseDelay: TimeInterval = 2.0
    static let syncAnchorPrefix = "healthkit_anchor_"
    static let aggregatedSyncDatePrefix = "healthkit_aggsync_"
    static let defaultHistoryDays = 730
    static let lastSyncDateKey = "lastHealthSyncDate"
    static let syncLogKey = "healthSyncLogs"
    static let maxSyncLogEntries = 50
}
