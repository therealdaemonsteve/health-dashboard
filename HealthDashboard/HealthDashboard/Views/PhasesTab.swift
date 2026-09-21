import SwiftUI

// MARK: - Today's Checklist Item

struct ChecklistItem: Identifiable, Hashable {
    let id: String // unique key for persistence
    let name: String
    let dose: String
    let timing: String
    let kind: String // "supplement" or "medication"
}

// MARK: - Timing Helpers

private func isDueToday(timing: String) -> Bool {
    let t = timing.lowercased()

    // Skip paused / not started items
    if t.contains("paused") || t.contains("not started") || t.contains("exclude") {
        return false
    }

    // Check for day-of-week references (Mon, Tue, Wed, etc. or full names)
    let dayAbbrevs = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
    let dayFull = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
    let weekdayIndex = Calendar.current.component(.weekday, from: Date()) - 1 // 0=Sun
    let hasDayRef = dayAbbrevs.contains(where: { t.contains($0) }) || dayFull.contains(where: { t.contains($0) })

    if hasDayRef {
        // Has specific day references — check if today matches
        return t.contains(dayAbbrevs[weekdayIndex]) || t.contains(dayFull[weekdayIndex])
    }

    // "daily", "morning", "evening", "bedtime", "with food", etc. → every day
    return true
}

private func todayDateString() -> String {
    Formatters.today
}

@Observable
class PhasesViewModel {
    var phases: [PhaseItem] = []
    var isLoading = false
    var error: String?
    var checkedIds: Set<String> = []

    var activePhases: [PhaseItem] {
        phases.filter { $0.status == "active" }
    }

    var completedPhases: [PhaseItem] {
        phases.filter { $0.status == "completed" }
    }

    var todayItems: [ChecklistItem] {
        var items: [ChecklistItem] = []
        for phase in activePhases {
            for s in phase.supplements ?? [] where isDueToday(timing: s.timing) {
                items.append(ChecklistItem(id: "s_\(phase.id)_\(s.name)", name: s.name, dose: s.dose, timing: s.timing, kind: "supplement"))
            }
            for m in phase.medications ?? [] where isDueToday(timing: m.timing) {
                items.append(ChecklistItem(id: "m_\(phase.id)_\(m.name)", name: m.name, dose: m.dose, timing: m.timing, kind: "medication"))
            }
        }
        return items
    }

    func load() async {
        isLoading = true
        error = nil
        do {
            let response = try await MCPClient.shared.getPhases()
            phases = response.phases
        } catch {
            self.error = error.localizedDescription
        }
        // Load checklist state from server
        do {
            let cl = try await MCPClient.shared.getChecklist(date: todayDateString())
            checkedIds = Set(cl.checked)
        } catch {
            // fallback: empty
        }
        isLoading = false
    }

    func refreshChecklist() async {
        do {
            let cl = try await MCPClient.shared.getChecklist(date: todayDateString())
            checkedIds = Set(cl.checked)
        } catch {
            // keep existing state
        }
    }

    func toggle(_ item: ChecklistItem) {
        // Optimistic update
        if checkedIds.contains(item.id) {
            checkedIds.remove(item.id)
        } else {
            checkedIds.insert(item.id)
        }
        // Persist to server
        Task {
            do {
                let result = try await MCPClient.shared.toggleChecklist(itemId: item.id, date: todayDateString())
                checkedIds = Set(result.checked)
            } catch {
                // revert on failure
                if checkedIds.contains(item.id) {
                    checkedIds.remove(item.id)
                } else {
                    checkedIds.insert(item.id)
                }
            }
        }
    }
}

struct PhasesTab: View {
    @State private var vm = PhasesViewModel()
    @State private var showAddPhase = false

    var body: some View {
        NavigationStack {
            ScrollView {
                if vm.isLoading && vm.phases.isEmpty {
                    ProgressView("Loading...")
                        .padding(.top, 60)
                } else if let error = vm.error, vm.phases.isEmpty {
                    ContentUnavailableView("Error", systemImage: "exclamationmark.triangle", description: Text(error))
                } else {
                    content
                }
            }
            .navigationTitle("Phases")
            .refreshable { await vm.load() }
            .task { await vm.load() }
            .onAppear { Task { await vm.refreshChecklist() } }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { showAddPhase = true } label: {
                        Image(systemName: "plus")
                    }
                }
            }
            .sheet(isPresented: $showAddPhase) {
                AddPhaseSheet(onSave: { await vm.load() })
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        VStack(alignment: .leading, spacing: 24) {
            // Today's Checklist
            if !vm.todayItems.isEmpty {
                TodayChecklist(items: vm.todayItems, checkedIds: vm.checkedIds, onToggle: { vm.toggle($0) })
            }

            // Active Phases
            if !vm.activePhases.isEmpty {
                section("Active", systemImage: "flag.fill") {
                    ForEach(vm.activePhases) { phase in
                        PhaseCard(phase: phase, isActive: true, onUpdate: { await vm.load() })
                    }
                }
            }

            // Completed Phases
            if !vm.completedPhases.isEmpty {
                section("Completed", systemImage: "checkmark.circle") {
                    ForEach(vm.completedPhases) { phase in
                        PhaseCard(phase: phase, isActive: false, onUpdate: { await vm.load() })
                    }
                }
            }

            if vm.phases.isEmpty {
                ContentUnavailableView("No Phases", systemImage: "list.bullet.clipboard", description: Text("Tap + to create your first training phase."))
                    .padding(.top, 40)
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

// MARK: - Phase Card

private struct PhaseCard: View {
    let phase: PhaseItem
    let isActive: Bool
    let onUpdate: () async -> Void
    @State private var isExpanded: Bool = false
    @State private var showEditSheet = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header - always visible
            Button { withAnimation { isExpanded.toggle() } } label: {
                header
            }
            .buttonStyle(.plain)

            // Expandable detail
            if isExpanded || isActive {
                detail
                    .padding(.top, 10)
            }
        }
        .padding(14)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(isActive ? Color.blue.opacity(0.3) : Color.clear, lineWidth: 1.5)
        )
        .sheet(isPresented: $showEditSheet) {
            EditPhaseSheet(phase: phase, onSave: onUpdate)
        }
        .onAppear {
            if isActive { isExpanded = true }
        }
    }

    @ViewBuilder
    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text(phase.name)
                    .font(.headline)
                if let startDate = phase.startDate {
                    let dateRange = phase.endDate != nil ? "\(startDate) - \(phase.endDate!)" : "Started \(startDate)"
                    Text(dateRange)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
            statusBadge
            Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private var detail: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Target
            if let target = phase.target, !target.isEmpty {
                detailRow("Target", systemImage: "target") {
                    Text(target)
                        .font(.callout)
                }
            }

            // Supplements
            if let supplements = phase.supplements, !supplements.isEmpty {
                detailRow("Supplements", systemImage: "pills") {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(supplements, id: \.self) { supp in
                            HStack {
                                Text(supp.name)
                                    .font(.callout)
                                Spacer()
                                Text(supp.dose)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Text(supp.timing)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }

            // Medications
            if let medications = phase.medications, !medications.isEmpty {
                detailRow("Medications", systemImage: "cross.case") {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(medications, id: \.self) { med in
                            HStack {
                                Text(med.name)
                                    .font(.callout)
                                Spacer()
                                Text(med.dose)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Text(med.timing)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }

            // Notes
            if let notes = phase.notes, !notes.isEmpty {
                detailRow("Notes", systemImage: "note.text") {
                    Text(notes)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
            }

            // Actions
            HStack(spacing: 12) {
                Button("Edit") { showEditSheet = true }
                    .font(.caption)

                if isActive {
                    Button("Complete Phase") {
                        Task {
                            _ = try? await MCPClient.shared.updatePhase(
                                phaseId: phase.id,
                                status: "completed",
                                endDate: Formatters.today
                            )
                            await onUpdate()
                        }
                    }
                    .font(.caption)
                    .foregroundStyle(.green)
                }
            }
            .padding(.top, 4)
        }
    }

    @ViewBuilder
    private func detailRow<Content: View>(_ title: String, systemImage: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(title, systemImage: systemImage)
                .font(.caption)
                .foregroundStyle(.secondary)
            content()
        }
    }

    @ViewBuilder
    private var statusBadge: some View {
        let color: Color = phase.status == "active" ? .blue : .green
        Text(phase.status?.capitalized ?? "")
            .font(.caption2)
            .fontWeight(.semibold)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color.opacity(0.15))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }
}

// MARK: - Add Phase Sheet

struct AddPhaseSheet: View {
    let onSave: () async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var startDate = Date()
    @State private var target = ""
    @State private var notes = ""
    @State private var supplements: [SupplementField] = []
    @State private var medications: [MedicationField] = []
    @State private var isSaving = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Phase") {
                    TextField("Name (e.g. 2026.5)", text: $name)
                    DatePicker("Start Date", selection: $startDate, displayedComponents: .date)
                    TextField("Target (optional)", text: $target)
                }

                Section("Supplements") {
                    ForEach($supplements) { $supp in
                        VStack(spacing: 6) {
                            TextField("Name", text: $supp.name)
                            HStack {
                                TextField("Dose", text: $supp.dose)
                                TextField("Timing", text: $supp.timing)
                            }
                            .font(.callout)
                        }
                    }
                    .onDelete { supplements.remove(atOffsets: $0) }
                    Button("Add Supplement") {
                        supplements.append(SupplementField())
                    }
                }

                Section("Medications") {
                    ForEach($medications) { $med in
                        VStack(spacing: 6) {
                            TextField("Name", text: $med.name)
                            HStack {
                                TextField("Dose", text: $med.dose)
                                TextField("Timing", text: $med.timing)
                            }
                            .font(.callout)
                        }
                    }
                    .onDelete { medications.remove(atOffsets: $0) }
                    Button("Add Medication") {
                        medications.append(MedicationField())
                    }
                }

                Section("Notes") {
                    TextEditor(text: $notes)
                        .frame(minHeight: 80)
                }
            }
            .navigationTitle("New Phase")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task { await save() }
                    }
                    .disabled(name.isEmpty || isSaving)
                }
            }
        }
    }

    private func save() async {
        isSaving = true
        let supps = supplements.filter { !$0.name.isEmpty }.map { Supplement(name: $0.name, dose: $0.dose, timing: $0.timing) }
        let meds = medications.filter { !$0.name.isEmpty }.map { Medication(name: $0.name, dose: $0.dose, timing: $0.timing) }

        _ = try? await MCPClient.shared.addPhase(
            name: name,
            startDate: Formatters.dateString(from: startDate),
            target: target.isEmpty ? nil : target,
            supplements: supps.isEmpty ? nil : supps,
            medications: meds.isEmpty ? nil : meds,
            notes: notes.isEmpty ? nil : notes
        )
        await onSave()
        dismiss()
    }
}

// MARK: - Edit Phase Sheet

struct EditPhaseSheet: View {
    let phase: PhaseItem
    let onSave: () async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var target: String
    @State private var notes: String
    @State private var supplements: [SupplementField]
    @State private var medications: [MedicationField]
    @State private var isSaving = false

    init(phase: PhaseItem, onSave: @escaping () async -> Void) {
        self.phase = phase
        self.onSave = onSave
        _name = State(initialValue: phase.name)
        _target = State(initialValue: phase.target ?? "")
        _notes = State(initialValue: phase.notes ?? "")
        _supplements = State(initialValue: (phase.supplements ?? []).map { SupplementField(name: $0.name, dose: $0.dose, timing: $0.timing) })
        _medications = State(initialValue: (phase.medications ?? []).map { MedicationField(name: $0.name, dose: $0.dose, timing: $0.timing) })
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Phase") {
                    TextField("Name", text: $name)
                    TextField("Target", text: $target)
                }

                Section("Supplements") {
                    ForEach($supplements) { $supp in
                        VStack(spacing: 6) {
                            TextField("Name", text: $supp.name)
                            HStack {
                                TextField("Dose", text: $supp.dose)
                                TextField("Timing", text: $supp.timing)
                            }
                            .font(.callout)
                        }
                    }
                    .onDelete { supplements.remove(atOffsets: $0) }
                    Button("Add Supplement") {
                        supplements.append(SupplementField())
                    }
                }

                Section("Medications") {
                    ForEach($medications) { $med in
                        VStack(spacing: 6) {
                            TextField("Name", text: $med.name)
                            HStack {
                                TextField("Dose", text: $med.dose)
                                TextField("Timing", text: $med.timing)
                            }
                            .font(.callout)
                        }
                    }
                    .onDelete { medications.remove(atOffsets: $0) }
                    Button("Add Medication") {
                        medications.append(MedicationField())
                    }
                }

                Section("Notes") {
                    TextEditor(text: $notes)
                        .frame(minHeight: 80)
                }
            }
            .navigationTitle("Edit Phase")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task { await save() }
                    }
                    .disabled(name.isEmpty || isSaving)
                }
            }
        }
    }

    private func save() async {
        isSaving = true

        let supps = supplements.filter { !$0.name.isEmpty }.map { Supplement(name: $0.name, dose: $0.dose, timing: $0.timing) }
        let meds = medications.filter { !$0.name.isEmpty }.map { Medication(name: $0.name, dose: $0.dose, timing: $0.timing) }

        _ = try? await MCPClient.shared.updatePhase(
            phaseId: phase.id,
            name: name != phase.name ? name : nil,
            target: target.isEmpty ? nil : target,
            supplements: supps,
            medications: meds,
            notes: notes
        )
        await onSave()
        dismiss()
    }
}

// MARK: - Today's Checklist

private struct TodayChecklist: View {
    let items: [ChecklistItem]
    let checkedIds: Set<String>
    let onToggle: (ChecklistItem) -> Void

    private var doneCount: Int { items.filter { checkedIds.contains($0.id) }.count }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("Today", systemImage: "calendar")
                    .font(.headline)
                Spacer()
                Text("\(doneCount)/\(items.count)")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundStyle(doneCount == items.count ? .green : .secondary)
            }

            if doneCount == items.count {
                HStack {
                    Spacer()
                    Label("All done!", systemImage: "checkmark.seal.fill")
                        .font(.subheadline)
                        .foregroundStyle(.green)
                    Spacer()
                }
                .padding(.vertical, 8)
            }

            ForEach(items) { item in
                let isDone = checkedIds.contains(item.id)
                Button { onToggle(item) } label: {
                    HStack(spacing: 12) {
                        Image(systemName: isDone ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(isDone ? .green : .secondary)
                            .font(.title3)

                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.name)
                                .fontWeight(.medium)
                                .strikethrough(isDone)
                                .foregroundStyle(isDone ? .secondary : .primary)
                            Text("\(item.dose) · \(item.timing)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }

                        Spacer()

                        let icon = item.kind == "medication" ? "cross.case" : "pills"
                        Image(systemName: icon)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(10)
                    .background(isDone ? Color.green.opacity(0.05) : Color(.secondarySystemBackground))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(14)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.orange.opacity(0.3), lineWidth: 1.5)
        )
    }
}

// MARK: - Form Field Types

private struct SupplementField: Identifiable {
    let id = UUID()
    var name = ""
    var dose = ""
    var timing = ""
}

private struct MedicationField: Identifiable {
    let id = UUID()
    var name = ""
    var dose = ""
    var timing = ""
}
