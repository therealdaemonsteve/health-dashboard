import Foundation

// MARK: - Biomarker List Item

struct BiomarkerSummary: Codable, Identifiable, Sendable {
    var id: String { name }
    let name: String
    let category: String
    let latestValue: Double?
    let latestDate: String?
    let unit: String?
    let status: RAGStatus?
    let measurementCount: Int?

    enum CodingKeys: String, CodingKey {
        case name, category, unit, status
        case latestValue = "latest_value"
        case latestDate = "latest_date"
        case measurementCount = "measurement_count"
    }
}

// MARK: - Biomarker Detail

struct BiomarkerDetail: Codable, Sendable {
    let name: String
    let category: String?
    let units: [String]?
    let status: RAGStatus?
    let stats: BiomarkerStats?
    let reference: ReferenceRange?
    let insight: String?
    let recentMeasurements: [Measurement]?

    enum CodingKeys: String, CodingKey {
        case name, category, units, status, stats, reference, insight
        case recentMeasurements = "recent_measurements"
    }
}

// MARK: - Stats

struct BiomarkerStats: Codable, Sendable {
    let firstValue: Double?
    let firstDate: String?
    let latestValue: Double?
    let latestDate: String?
    let min: Double?
    let max: Double?
    let mean: Double?
    let median: Double?
    let n: Int?
    let spanDays: Int?
    let pctChange: Double?

    enum CodingKeys: String, CodingKey {
        case min, max, mean, median, n
        case firstValue = "first_value"
        case firstDate = "first_date"
        case latestValue = "latest_value"
        case latestDate = "latest_date"
        case spanDays = "span_days"
        case pctChange = "pct_change"
    }
}

// MARK: - Reference Range

struct ReferenceRange: Codable, Sendable {
    let unit: String?
    let green: [Double]?
    let amber: [[Double]]?
    let redLow: Double?
    let redHigh: Double?
    let oneSided: String?
    let tag: String?

    enum CodingKeys: String, CodingKey {
        case unit, green, amber, tag
        case redLow = "red_low"
        case redHigh = "red_high"
        case oneSided = "one_sided"
    }
}

// MARK: - Measurement

struct Measurement: Codable, Identifiable, Sendable {
    var id: String { measurementId ?? UUID().uuidString }
    let measurementId: String?
    let source: String?
    let sourceLabel: String?
    let testId: String?
    let testName: String?
    let date: String
    let biomarker: String?
    let biomarkerRaw: String?
    let category: String?
    let value: Double
    let valueRaw: String?
    let qualifier: String?
    let unit: String
    let status: RAGStatus?
    let statusRaw: String?
    let referenceRange: String?
    let rag: RAGStatus?
    let flaggedErroneous: Bool?

    enum CodingKeys: String, CodingKey {
        case source, date, biomarker, category, value, qualifier, unit, status, rag
        case measurementId = "id"
        case sourceLabel = "source_label"
        case testId = "test_id"
        case testName = "test_name"
        case biomarkerRaw = "biomarker_raw"
        case valueRaw = "value_raw"
        case statusRaw = "status_raw"
        case referenceRange = "reference_range"
        case flaggedErroneous = "flagged_erroneous"
    }
}

// MARK: - Measurements Response

struct MeasurementsResponse: Codable, Sendable {
    let biomarker: String
    let unit: String?
    let count: Int
    let measurements: [Measurement]
}

// MARK: - Flagged Biomarker

struct FlaggedBiomarker: Codable, Identifiable, Sendable {
    var id: String { name }
    let name: String
    let category: String?
    let status: RAGStatus?
    let latestValue: Double?
    let latestDate: String?
    let unit: String?
    let reference: ReferenceRange?
    let insight: String?
    let pctChange: Double?

    enum CodingKeys: String, CodingKey {
        case name, category, status, unit, reference, insight
        case latestValue = "latest_value"
        case latestDate = "latest_date"
        case pctChange = "pct_change"
    }
}

// MARK: - Add Measurement Response

struct AddMeasurementResponse: Codable, Sendable {
    let status: String
    let measurement: AddedMeasurement?
    let matchedBiomarker: String?

    enum CodingKeys: String, CodingKey {
        case status, measurement
        case matchedBiomarker = "matched_biomarker"
    }
}

struct AddedMeasurement: Codable, Sendable {
    let id: String
    let source: String
    let sourceLabel: String?
    let biomarker: String
    let date: String
    let value: Double
    let unit: String

    enum CodingKeys: String, CodingKey {
        case id, source, biomarker, date, value, unit
        case sourceLabel = "source_label"
    }
}

struct DeleteMeasurementResponse: Codable, Sendable {
    let status: String
    let measurementId: String?

    enum CodingKeys: String, CodingKey {
        case status
        case measurementId = "measurement_id"
    }
}

// MARK: - Rolling Average Response (Feature 1)

struct RollingAverageResponse: Codable, Sendable {
    let biomarker: String
    let unit: String?
    let windowDays: Int
    let raw: [DataPoint]
    let rollingAverage: [DataPoint]

    enum CodingKeys: String, CodingKey {
        case biomarker, unit, raw
        case windowDays = "window_days"
        case rollingAverage = "rolling_average"
    }
}

struct DataPoint: Codable, Sendable, Identifiable {
    var id: String { date }
    let date: String
    let value: Double
}

// MARK: - Correlation Response (Feature 2)

struct CorrelationResponse: Codable, Sendable {
    let biomarkerA: String
    let biomarkerB: String
    let nAligned: Int
    let pearson: PearsonResult
    let spearman: SpearmanResult
    let lagAnalysis: LagAnalysis?
    let interpretation: String?

    enum CodingKeys: String, CodingKey {
        case pearson, spearman, interpretation
        case biomarkerA = "biomarker_a"
        case biomarkerB = "biomarker_b"
        case nAligned = "n_aligned"
        case lagAnalysis = "lag_analysis"
    }
}

struct PearsonResult: Codable, Sendable {
    let r: Double
    let p: Double
}

struct SpearmanResult: Codable, Sendable {
    let rho: Double
    let p: Double
}

struct LagAnalysis: Codable, Sendable {
    let optimalLagDays: Int
    let maxCorrelation: Double
    let direction: String?

    enum CodingKeys: String, CodingKey {
        case direction
        case optimalLagDays = "optimal_lag_days"
        case maxCorrelation = "max_correlation"
    }
}

// MARK: - Trend Detection Response (Feature 4)

struct TrendDetectionResponse: Codable, Sendable {
    let lookbackDays: Int
    let trends: [BiomarkerTrend]
    let summary: TrendSummary

    enum CodingKeys: String, CodingKey {
        case trends, summary
        case lookbackDays = "lookback_days"
    }
}

struct BiomarkerTrend: Codable, Identifiable, Sendable {
    var id: String { biomarker }
    let biomarker: String
    let unit: String?
    let currentValue: Double?
    let currentStatus: String?
    let slopePerDay: Double?
    let direction: String?
    let rSquared: Double?
    let n: Int?
    let alertLevel: String?
    let daysToRed: Int?
    let daysToAmber: Int?
    let projection: String?

    enum CodingKeys: String, CodingKey {
        case biomarker, unit, direction, n, projection
        case currentValue = "current_value"
        case currentStatus = "current_status"
        case slopePerDay = "slope_per_day"
        case rSquared = "r_squared"
        case alertLevel = "alert_level"
        case daysToRed = "days_to_red"
        case daysToAmber = "days_to_amber"
    }
}

struct TrendSummary: Codable, Sendable {
    let critical: Int
    let warning: Int
    let watch: Int
    let stable: Int
}

// MARK: - RAG Status

enum RAGStatus: String, Codable, Sendable {
    case green
    case amber
    case red
    case unknown

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        let raw = try container.decode(String.self).lowercased()
        self = RAGStatus(rawValue: raw) ?? .unknown
    }
}
