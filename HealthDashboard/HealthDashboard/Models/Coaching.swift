import Foundation

// MARK: - Coaching Brief

struct CoachingBrief: Codable, Sendable {
    let activeGoals: [Goal]?
    let achievedGoals90d: [Goal]?
    let pendingActionItems: [ActionItem]?
    let recentCompletedActions30d: [ActionItem]?
    let recentCoachingNotes: [CoachingNote]?
    let healthSnapshot: HealthSnapshot?

    enum CodingKeys: String, CodingKey {
        case healthSnapshot = "health_snapshot"
        case activeGoals = "active_goals"
        case achievedGoals90d = "achieved_goals_90d"
        case pendingActionItems = "pending_action_items"
        case recentCompletedActions30d = "recent_completed_actions_30d"
        case recentCoachingNotes = "recent_coaching_notes"
    }
}

// MARK: - Goal

struct Goal: Codable, Identifiable, Sendable {
    let id: String
    let title: String
    let status: String?
    let category: String?
    let targetValue: Double?
    let targetUnit: String?
    let targetDate: String?
    let linkedBiomarker: String?
    let progressNotes: [ProgressNote]?
    let createdAt: String?
    let updatedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, title, status, category
        case targetValue = "target_value"
        case targetUnit = "target_unit"
        case targetDate = "target_date"
        case linkedBiomarker = "linked_biomarker"
        case progressNotes = "progress_notes"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct ProgressNote: Codable, Sendable {
    let date: String
    let note: String
}

// MARK: - Action Item

struct ActionItem: Codable, Identifiable, Sendable {
    let id: String
    let title: String
    let status: String?
    let dueDate: String?
    let goalId: String?
    let createdAt: String?
    let completedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, title, status
        case dueDate = "due_date"
        case goalId = "goal_id"
        case createdAt = "created_at"
        case completedAt = "completed_at"
    }
}

// MARK: - Coaching Note

struct CoachingNote: Codable, Identifiable, Sendable {
    let id: String
    let date: String
    let text: String
    let tags: [String]?
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id, date, text, tags
        case createdAt = "created_at"
    }
}

// MARK: - Health Snapshot (embedded in coaching brief)

struct HealthSnapshot: Codable, Sendable {
    let headline: String?
    let flaggedBiomarkers: [SnapshotBiomarker]?
    let recentEvents: [HealthEvent]?
    let latestWeight: SnapshotMetric?
    let latestBodyFat: SnapshotMetric?
    let recommendations: [Recommendation]?

    enum CodingKeys: String, CodingKey {
        case headline, recommendations
        case flaggedBiomarkers = "flagged_biomarkers"
        case recentEvents = "recent_events"
        case latestWeight = "latest_weight"
        case latestBodyFat = "latest_body_fat"
    }
}

struct SnapshotBiomarker: Codable, Identifiable, Sendable {
    var id: String { name }
    let name: String
    let status: String?
    let latestValue: Double?
    let unit: String?

    enum CodingKeys: String, CodingKey {
        case name, status, unit
        case latestValue = "latest_value"
    }
}

struct SnapshotMetric: Codable, Sendable {
    let value: Double
    let date: String
    let unit: String
}

// MARK: - Goal Progress Response (Feature 5)

struct GoalProgressResponse: Codable, Sendable {
    let goals: [GoalProgress]
}

struct GoalProgress: Codable, Identifiable, Sendable {
    var id: String { goalId }
    let goalId: String
    let title: String
    let linkedBiomarker: String?
    let startValue: Double?
    let currentValue: Double?
    let targetValue: Double?
    let progressPct: Double?
    let ratePerDay: Double?
    let targetDate: String?
    let projectedCompletion: String?
    let statusVsSchedule: String?
    let daysRemaining: Int?

    enum CodingKeys: String, CodingKey {
        case title
        case goalId = "goal_id"
        case linkedBiomarker = "linked_biomarker"
        case startValue = "start_value"
        case currentValue = "current_value"
        case targetValue = "target_value"
        case progressPct = "progress_pct"
        case ratePerDay = "rate_per_day"
        case targetDate = "target_date"
        case projectedCompletion = "projected_completion"
        case statusVsSchedule = "status_vs_schedule"
        case daysRemaining = "days_remaining"
    }
}

// MARK: - Mutation Responses

struct AddGoalResponse: Codable, Sendable {
    let status: String
    let goal: Goal?
}

struct UpdateGoalResponse: Codable, Sendable {
    let status: String
    let goal: Goal?
}

struct AddCoachingNoteResponse: Codable, Sendable {
    let status: String
    let note: CoachingNote?
}

struct AddActionItemResponse: Codable, Sendable {
    let status: String
    let actionItem: ActionItem?

    enum CodingKeys: String, CodingKey {
        case status
        case actionItem = "action_item"
    }
}

struct UpdateActionItemResponse: Codable, Sendable {
    let status: String
    let actionItem: ActionItem?

    enum CodingKeys: String, CodingKey {
        case status
        case actionItem = "action_item"
    }
}
