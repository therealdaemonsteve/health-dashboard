import Foundation

struct HealthRecord: Codable {
    let metric: String
    let value: Double
    let date: String
    let unit: String
}

struct SyncLogEntry: Codable, Identifiable {
    let id: UUID
    let timestamp: Date
    let trigger: SyncTrigger
    let recordsSent: Int
    let recordsImported: Int
    let skippedDuplicate: Int
    let duration: TimeInterval
    let success: Bool
    let errorMessage: String?

    enum SyncTrigger: String, Codable {
        case manual
        case backgroundRefresh
        case workoutDelivery
        case nutritionDelivery
        case appLaunch
    }
}

struct AppleHealthImportResponse: Codable {
    let status: String
    let totalRecords: Int?
    let imported: Int?
    let skippedUnmapped: Int?
    let skippedNonNumeric: Int?
    let skippedArtefact: Int?
    let skippedDuplicate: Int?
    let perMetric: [String: Int]?
    let s3Written: Bool?

    enum CodingKeys: String, CodingKey {
        case status
        case totalRecords = "total_records"
        case imported
        case skippedUnmapped = "skipped_unmapped"
        case skippedNonNumeric = "skipped_non_numeric"
        case skippedArtefact = "skipped_artefact"
        case skippedDuplicate = "skipped_duplicate"
        case perMetric = "per_metric"
        case s3Written = "s3_written"
    }
}
