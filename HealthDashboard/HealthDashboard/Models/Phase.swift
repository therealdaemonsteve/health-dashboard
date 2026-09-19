import Foundation

// MARK: - Phase Item

struct PhaseItem: Codable, Identifiable, Sendable {
    let id: String
    let name: String
    let status: String?
    let startDate: String?
    let endDate: String?
    let target: String?
    let supplements: [Supplement]?
    let medications: [Medication]?
    let notes: String?
    let createdAt: String?
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, name, status, target, supplements, medications, notes
        case startDate = "start_date"
        case endDate = "end_date"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

// MARK: - Supplement

struct Supplement: Codable, Sendable, Hashable {
    let name: String
    let dose: String
    let timing: String
}

// MARK: - Medication

struct Medication: Codable, Sendable, Hashable {
    let name: String
    let dose: String
    let timing: String
}

// MARK: - Phases List Response

struct PhasesListResponse: Codable, Sendable {
    let count: Int
    let phases: [PhaseItem]
}

// MARK: - Mutation Responses

struct AddPhaseResponse: Codable, Sendable {
    let status: String
    let phase: PhaseItem?
}

struct UpdatePhaseResponse: Codable, Sendable {
    let status: String
    let phase: PhaseItem?
}

// MARK: - Checklist Responses

struct ChecklistResponse: Codable, Sendable {
    let date: String
    let checked: [String]
}

struct ToggleChecklistResponse: Codable, Sendable {
    let status: String
    let itemId: String?
    let date: String?
    let checked: [String]

    enum CodingKeys: String, CodingKey {
        case status, date, checked
        case itemId = "item_id"
    }
}
