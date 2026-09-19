import SwiftUI

@Observable
class CoachingViewModel {
    var brief: CoachingBrief?
    var goalProgress: [String: GoalProgress] = [:]
    var isLoading = false
    var error: String?

    func load() async {
        isLoading = true
        error = nil
        do {
            async let b = MCPClient.shared.getCoachingBrief()
            async let gp = MCPClient.shared.getGoalProgress()
            let (briefResult, progressResult) = try await (b, gp)
            brief = briefResult
            goalProgress = Dictionary(
                uniqueKeysWithValues: progressResult.goals.map { ($0.goalId, $0) }
            )
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }
}

struct CoachingTab: View {
    @State private var vm = CoachingViewModel()
    @State private var showAddGoal = false
    @State private var showAddAction = false
    @State private var showAddNote = false

    var body: some View {
        NavigationStack {
            ScrollView {
                if vm.isLoading && vm.brief == nil {
                    ProgressView("Loading...")
                        .padding(.top, 60)
                } else if let error = vm.error, vm.brief == nil {
                    ContentUnavailableView("Error", systemImage: "exclamationmark.triangle", description: Text(error))
                } else if let brief = vm.brief {
                    content(brief)
                }
            }
            .navigationTitle("Coaching")
            .refreshable { await vm.load() }
            .task { await vm.load() }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        Button("Add Goal", systemImage: "target") { showAddGoal = true }
                        Button("Add Action Item", systemImage: "checklist") { showAddAction = true }
                        Button("Add Note", systemImage: "note.text.badge.plus") { showAddNote = true }
                    } label: {
                        Image(systemName: "plus")
                    }
                }
            }
            .sheet(isPresented: $showAddGoal) { AddGoalSheet(onSave: { await vm.load() }) }
            .sheet(isPresented: $showAddAction) { AddActionSheet(goals: vm.brief?.activeGoals ?? [], onSave: { await vm.load() }) }
            .sheet(isPresented: $showAddNote) { AddNoteSheet(onSave: { await vm.load() }) }
        }
    }

    @ViewBuilder
    private func content(_ brief: CoachingBrief) -> some View {
        VStack(alignment: .leading, spacing: 24) {
            // Active Goals
            if let goals = brief.activeGoals, !goals.isEmpty {
                section("Active Goals", systemImage: "target") {
                    ForEach(goals) { goal in
                        GoalCard(goal: goal, progress: vm.goalProgress[goal.id], onUpdate: { await vm.load() })
                    }
                }
            }

            // Pending Action Items
            if let actions = brief.pendingActionItems, !actions.isEmpty {
                section("Action Items", systemImage: "checklist") {
                    ForEach(actions) { action in
                        ActionItemRow(action: action, onUpdate: { await vm.load() })
                    }
                }
            }

            // Recent Notes (sorted most recent first)
            if let notes = brief.recentCoachingNotes, !notes.isEmpty {
                section("Recent Notes", systemImage: "note.text") {
                    ForEach(notes.sorted(by: { $0.date > $1.date })) { note in
                        NoteCard(note: note)
                    }
                }
            }

            // Achieved Goals
            if let achieved = brief.achievedGoals90d, !achieved.isEmpty {
                section("Recently Achieved", systemImage: "checkmark.seal") {
                    ForEach(achieved) { goal in
                        GoalCard(goal: goal, onUpdate: { await vm.load() })
                    }
                }
            }
        }
        .padding()
    }

    @ViewBuilder
    private func section<Content: View>(_ title: String, systemImage: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: systemImage)
                .font(.headline)
            content()
        }
    }
}

// MARK: - Goal Card

private struct GoalCard: View {
    let goal: Goal
    var progress: GoalProgress?
    let onUpdate: () async -> Void
    @State private var showProgressSheet = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(goal.title)
                    .fontWeight(.medium)
                Spacer()
                goalStatusBadge
            }

            if let target = goalTarget {
                Text(target)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            // Progress bar
            if let p = progress, let pct = p.progressPct {
                VStack(alignment: .leading, spacing: 4) {
                    GeometryReader { geo in
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 3)
                                .fill(.quaternary)
                                .frame(height: 6)
                            RoundedRectangle(cornerRadius: 3)
                                .fill(progressColor(pct: pct, status: p.statusVsSchedule))
                                .frame(width: geo.size.width * min(max(pct, 0), 100) / 100, height: 6)
                        }
                    }
                    .frame(height: 6)

                    HStack {
                        if let current = p.currentValue, let target = p.targetValue {
                            Text("\(formatVal(current)) → \(formatVal(target))")
                                .font(.caption2)
                                .monospacedDigit()
                                .foregroundStyle(.secondary)
                        }
                        Text(String(format: "%.0f%%", pct))
                            .font(.caption2)
                            .fontWeight(.semibold)
                            .foregroundStyle(progressColor(pct: pct, status: p.statusVsSchedule))
                        Spacer()
                        if let status = p.statusVsSchedule {
                            Text(status.replacingOccurrences(of: "_", with: " ").capitalized)
                                .font(.caption2)
                                .padding(.horizontal, 5)
                                .padding(.vertical, 1)
                                .background(scheduleColor(status).opacity(0.15))
                                .foregroundStyle(scheduleColor(status))
                                .clipShape(Capsule())
                        }
                    }
                }
            }

            if let notes = goal.progressNotes, let last = notes.last {
                Text("\(last.date): \(last.note)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            HStack(spacing: 12) {
                if goal.status == "active" {
                    Button("Add Progress") { showProgressSheet = true }
                        .font(.caption)
                    Button("Mark Achieved") {
                        Task {
                            _ = try? await MCPClient.shared.updateGoal(goalId: goal.id, status: "achieved")
                            await onUpdate()
                        }
                    }
                    .font(.caption)
                    .foregroundStyle(.green)
                }
            }
        }
        .padding(12)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
        .sheet(isPresented: $showProgressSheet) {
            AddProgressSheet(goal: goal, onSave: onUpdate)
        }
    }

    private func formatVal(_ v: Double) -> String {
        v == v.rounded() && abs(v) < 10000 ? String(format: "%.0f", v) : String(format: "%.1f", v)
    }

    private func progressColor(pct: Double, status: String?) -> Color {
        if pct >= 100 { return .green }
        if status == "behind" { return .red }
        if status == "on_track" || status == "ahead" { return .blue }
        return .orange
    }

    private func scheduleColor(_ status: String) -> Color {
        switch status {
        case "ahead": return .green
        case "on_track": return .blue
        case "behind": return .red
        default: return .secondary
        }
    }

    private var goalTarget: String? {
        var parts: [String] = []
        if let val = goal.targetValue, let unit = goal.targetUnit {
            parts.append("Target: \(String(format: "%.1f", val)) \(unit)")
        }
        if let date = goal.targetDate {
            parts.append("By: \(date)")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " | ")
    }

    @ViewBuilder
    private var goalStatusBadge: some View {
        let color: Color = goal.status == "achieved" ? .green : goal.status == "active" ? .blue : .secondary
        Text(goal.status?.capitalized ?? "")
            .font(.caption2)
            .fontWeight(.semibold)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color.opacity(0.15))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }
}

// MARK: - Action Item Row

private struct ActionItemRow: View {
    let action: ActionItem
    let onUpdate: () async -> Void

    var body: some View {
        HStack {
            Button {
                Task {
                    _ = try? await MCPClient.shared.updateActionItem(actionId: action.id, status: "done")
                    await onUpdate()
                }
            } label: {
                Image(systemName: action.status == "done" ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(action.status == "done" ? .green : .secondary)
            }

            VStack(alignment: .leading, spacing: 2) {
                Text(action.title)
                    .fontWeight(.medium)
                    .strikethrough(action.status == "done")
                if let due = action.dueDate {
                    Text("Due: \(due)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Spacer()

            if action.status == "pending" {
                Menu {
                    Button("Mark Done") {
                        Task {
                            _ = try? await MCPClient.shared.updateActionItem(actionId: action.id, status: "done")
                            await onUpdate()
                        }
                    }
                    Button("Skip") {
                        Task {
                            _ = try? await MCPClient.shared.updateActionItem(actionId: action.id, status: "skipped")
                            await onUpdate()
                        }
                    }
                } label: {
                    Image(systemName: "ellipsis")
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(12)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
    }
}

// MARK: - Note Card

private struct NoteCard: View {
    let note: CoachingNote

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(note.date)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                if let tags = note.tags {
                    ForEach(tags, id: \.self) { tag in
                        Text(tag)
                            .font(.caption2)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(.blue.opacity(0.1))
                            .foregroundStyle(.blue)
                            .clipShape(Capsule())
                    }
                }
            }
            Text(note.text)
                .font(.callout)
        }
        .padding(12)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
    }
}

// MARK: - Add Goal Sheet

struct AddGoalSheet: View {
    let onSave: () async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var title = ""
    @State private var category = ""
    @State private var targetValue = ""
    @State private var targetUnit = ""
    @State private var targetDate = Date()
    @State private var hasTargetDate = false
    @State private var isSaving = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Goal") {
                    TextField("Title", text: $title)
                    TextField("Category (optional)", text: $category)
                }
                Section("Target (optional)") {
                    TextField("Target value", text: $targetValue)
                        .keyboardType(.decimalPad)
                    TextField("Unit", text: $targetUnit)
                    Toggle("Set target date", isOn: $hasTargetDate)
                    if hasTargetDate {
                        DatePicker("Date", selection: $targetDate, displayedComponents: .date)
                    }
                }
            }
            .navigationTitle("New Goal")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task { await save() }
                    }
                    .disabled(title.isEmpty || isSaving)
                }
            }
        }
    }

    private func save() async {
        isSaving = true
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd"

        _ = try? await MCPClient.shared.addGoal(
            title: title,
            category: category.isEmpty ? nil : category,
            targetValue: Double(targetValue),
            targetUnit: targetUnit.isEmpty ? nil : targetUnit,
            targetDate: hasTargetDate ? fmt.string(from: targetDate) : nil
        )
        await onSave()
        dismiss()
    }
}

// MARK: - Add Action Sheet

struct AddActionSheet: View {
    let goals: [Goal]
    let onSave: () async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var title = ""
    @State private var dueDate = Date()
    @State private var hasDueDate = false
    @State private var selectedGoalId: String?
    @State private var isSaving = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Action Item") {
                    TextField("Title", text: $title)
                }
                Section("Details") {
                    Toggle("Set due date", isOn: $hasDueDate)
                    if hasDueDate {
                        DatePicker("Due date", selection: $dueDate, displayedComponents: .date)
                    }
                    if !goals.isEmpty {
                        Picker("Link to goal", selection: $selectedGoalId) {
                            Text("None").tag(nil as String?)
                            ForEach(goals) { goal in
                                Text(goal.title).tag(goal.id as String?)
                            }
                        }
                    }
                }
            }
            .navigationTitle("New Action Item")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task { await save() }
                    }
                    .disabled(title.isEmpty || isSaving)
                }
            }
        }
    }

    private func save() async {
        isSaving = true
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd"

        _ = try? await MCPClient.shared.addActionItem(
            title: title,
            dueDate: hasDueDate ? fmt.string(from: dueDate) : nil,
            goalId: selectedGoalId
        )
        await onSave()
        dismiss()
    }
}

// MARK: - Add Note Sheet

struct AddNoteSheet: View {
    let onSave: () async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var tags = ""
    @State private var isSaving = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Note") {
                    TextEditor(text: $text)
                        .frame(minHeight: 120)
                }
                Section("Tags (comma-separated)") {
                    TextField("e.g. lipids, strategy", text: $tags)
                }
            }
            .navigationTitle("New Note")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task { await save() }
                    }
                    .disabled(text.isEmpty || isSaving)
                }
            }
        }
    }

    private func save() async {
        isSaving = true
        _ = try? await MCPClient.shared.addCoachingNote(
            text: text,
            tags: tags.isEmpty ? nil : tags
        )
        await onSave()
        dismiss()
    }
}

// MARK: - Add Progress Sheet

struct AddProgressSheet: View {
    let goal: Goal
    let onSave: () async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var note = ""
    @State private var isSaving = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Progress Note for: \(goal.title)") {
                    TextEditor(text: $note)
                        .frame(minHeight: 100)
                }
            }
            .navigationTitle("Add Progress")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task { await save() }
                    }
                    .disabled(note.isEmpty || isSaving)
                }
            }
        }
    }

    private func save() async {
        isSaving = true
        _ = try? await MCPClient.shared.updateGoal(goalId: goal.id, progressNote: note)
        await onSave()
        dismiss()
    }
}
