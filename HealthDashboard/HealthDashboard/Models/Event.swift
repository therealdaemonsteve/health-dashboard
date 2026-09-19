import Foundation

struct HealthEvent: Codable, Identifiable, Sendable {
    let id: String
    let date: String
    let type: String
    let title: String
    let notes: String?

    var typeIcon: String {
        switch type {
        case "trt_dose": return "syringe"
        case "supplement_change": return "pill"
        case "scan_dexa": return "figure.stand"
        case "blood_panel": return "drop.fill"
        case "peptide_start", "peptide_pause": return "atom"
        case "travel": return "airplane"
        case "training_change": return "dumbbell"
        case "life_event": return "star"
        case "diet_change": return "fork.knife"
        case "lifestyle": return "heart.circle"
        case "medical": return "cross.case"
        default: return "note.text"
        }
    }

    var typeColor: String {
        switch type {
        case "trt_dose": return "blue"
        case "supplement_change": return "purple"
        case "blood_panel": return "red"
        case "scan_dexa": return "orange"
        case "training_change": return "green"
        default: return "gray"
        }
    }
}

struct AddEventResponse: Codable, Sendable {
    let status: String
    let event: HealthEvent?
}

// MARK: - Event Impact Response (Feature 3)

struct EventImpactResponse: Codable, Sendable {
    let event: EventInfo
    let windowDays: Int
    let biomarkerImpacts: [BiomarkerImpact]

    enum CodingKeys: String, CodingKey {
        case event
        case windowDays = "window_days"
        case biomarkerImpacts = "biomarker_impacts"
    }
}

struct EventInfo: Codable, Sendable {
    let id: String
    let date: String
    let type: String
    let title: String
}

struct BiomarkerImpact: Codable, Identifiable, Sendable {
    var id: String { biomarker }
    let biomarker: String
    let unit: String?
    let before: PeriodStats
    let after: PeriodStats
    let changePct: Double?
    let direction: String?
    let likelySignificant: Bool?

    enum CodingKeys: String, CodingKey {
        case biomarker, unit, before, after, direction
        case changePct = "change_pct"
        case likelySignificant = "likely_significant"
    }
}

struct PeriodStats: Codable, Sendable {
    let mean: Double?
    let median: Double?
    let n: Int?
    let trendSlope: Double?

    enum CodingKeys: String, CodingKey {
        case mean, median, n
        case trendSlope = "trend_slope"
    }
}

// MARK: - Generate Insights Response

struct GenerateInsightsResponse: Codable, Sendable {
    let status: String
    let biomarkersUpdated: Int?
    let insights: [GeneratedInsight]?

    enum CodingKeys: String, CodingKey {
        case status, insights
        case biomarkersUpdated = "biomarkers_updated"
    }
}

struct GeneratedInsight: Codable, Sendable {
    let biomarker: String
    let insight: String
}
