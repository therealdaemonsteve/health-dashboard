import Foundation

// MARK: - Health Overview

struct HealthOverview: Codable, Sendable {
    let recipient: String?
    let generatedAt: String?
    let dataLoadedAt: String?
    let totalBiomarkers: Int?
    let statusCounts: StatusCounts?
    let headline: String?
    let categories: [OverviewCategory]?
    let recommendations: [Recommendation]?

    enum CodingKeys: String, CodingKey {
        case recipient, headline, categories, recommendations
        case generatedAt = "generated_at"
        case dataLoadedAt = "data_loaded_at"
        case totalBiomarkers = "total_biomarkers"
        case statusCounts = "status_counts"
    }
}

struct StatusCounts: Codable, Sendable {
    let green: Int
    let amber: Int
    let red: Int
    let unknown: Int

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        let dict = try container.decode([String: Int].self)
        green = dict["green"] ?? 0
        amber = dict["amber"] ?? 0
        red = dict["red"] ?? 0
        unknown = dict["unknown"] ?? 0
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(["green": green, "amber": amber, "red": red, "unknown": unknown])
    }
}

struct OverviewCategory: Codable, Identifiable, Sendable {
    var id: String { name ?? title ?? UUID().uuidString }

    // Backend uses "name"/"status"/"summary", support both field name conventions
    let name: String?
    let title: String?
    let status: String?
    let tone: String?
    let text: String?
    let summary: String?

    /// Display name, preferring "name" (backend) over "title"
    var displayName: String { name ?? title ?? "Unknown" }
    /// Display tone, preferring "status" (backend) over "tone"
    var displayTone: String? { status ?? tone }
    /// Display text, preferring "summary" (backend) over "text"
    var displayText: String { summary ?? text ?? "" }
}

struct Recommendation: Codable, Identifiable, Sendable {
    var id: String { title ?? text.prefix(40).description }

    let priority: PriorityCodable?
    let title: String?
    let text: String
}

/// Handles priority as either Int or String from the backend.
enum PriorityCodable: Codable, Sendable {
    case int(Int)
    case string(String)

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let intVal = try? container.decode(Int.self) {
            self = .int(intVal)
        } else if let strVal = try? container.decode(String.self) {
            self = .string(strVal)
        } else {
            self = .string("low")
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .int(let v): try container.encode(v)
        case .string(let v): try container.encode(v)
        }
    }

    var stringValue: String {
        switch self {
        case .int(let v): return v <= 1 ? "high" : v == 2 ? "medium" : "low"
        case .string(let v): return v
        }
    }
}

// MARK: - Health Scores Response (Feature 6)

struct HealthScoresResponse: Codable, Sendable {
    let overallScore: Double
    let overallGrade: String
    let categories: [CategoryScore]

    enum CodingKeys: String, CodingKey {
        case categories
        case overallScore = "overall_score"
        case overallGrade = "overall_grade"
    }
}

struct CategoryScore: Codable, Identifiable, Sendable {
    var id: String { category }
    let category: String
    let score: Double
    let grade: String
    let biomarkerCount: Int
    let biomarkerScores: [BiomarkerScoreItem]

    enum CodingKeys: String, CodingKey {
        case category, score, grade
        case biomarkerCount = "biomarker_count"
        case biomarkerScores = "biomarker_scores"
    }
}

struct BiomarkerScoreItem: Codable, Identifiable, Sendable {
    var id: String { biomarker }
    let biomarker: String
    let score: Double
    let status: String?
    let value: Double?
    let unit: String?
}

// MARK: - Category Summary

struct CategorySummary: Codable, Sendable {
    let category: String?
    let biomarkers: [BiomarkerSummary]?
    let summary: String?
}
