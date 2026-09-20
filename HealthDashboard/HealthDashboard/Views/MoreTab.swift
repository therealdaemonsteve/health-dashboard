import SwiftUI

@Observable
class MoreViewModel {
    var events: [HealthEvent] = []
    var isLoading = false
    var error: String?

    func load() async {
        isLoading = true
        error = nil
        do {
            events = try await MCPClient.shared.getEvents()
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }
}

struct MoreTab: View {
    @Binding var isAuthenticated: Bool
    @State private var vm = MoreViewModel()
    @State private var showAddMeasurement = false

    var body: some View {
        NavigationStack {
            List {
                Section("Quick Actions") {
                    Button {
                        showAddMeasurement = true
                    } label: {
                        Label("Add Measurement", systemImage: "plus.circle")
                    }
                }

                Section("Recent Events") {
                    if vm.isLoading && vm.events.isEmpty {
                        ProgressView()
                    } else if vm.events.isEmpty {
                        Text("No events")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(vm.events.prefix(30)) { event in
                            EventRow(event: event)
                        }
                    }
                }

                Section("Server") {
                    LabeledContent("URL") {
                        Text(AppConstants.apiBaseURL)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                }

                Section {
                    Button(role: .destructive) {
                        Task {
                            await MCPClient.shared.logout()
                            isAuthenticated = false
                        }
                    } label: {
                        Label("Sign Out", systemImage: "rectangle.portrait.and.arrow.right")
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("More")
            .refreshable { await vm.load() }
            .task { await vm.load() }
            .sheet(isPresented: $showAddMeasurement) {
                AddMeasurementSheet(onSave: { await vm.load() })
            }
        }
    }
}

// MARK: - Event Row

private struct EventRow: View {
    let event: HealthEvent

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: event.typeIcon)
                .foregroundStyle(typeColor)
                .frame(width: 24)

            VStack(alignment: .leading, spacing: 3) {
                Text(event.title)
                    .fontWeight(.medium)
                HStack {
                    Text(event.date)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(event.type.replacingOccurrences(of: "_", with: " "))
                        .font(.caption2)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 1)
                        .background(.quaternary)
                        .clipShape(Capsule())
                }
                if let notes = event.notes, !notes.isEmpty {
                    Text(notes)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }
        }
    }

    private var typeColor: Color {
        switch event.typeColor {
        case "blue": return .blue
        case "purple": return .purple
        case "red": return .red
        case "orange": return .orange
        case "green": return .green
        default: return .secondary
        }
    }
}

// MARK: - Add Measurement Sheet

struct AddMeasurementSheet: View {
    let onSave: () async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var biomarker = ""
    @State private var value = ""
    @State private var unit = ""
    @State private var date = Date()
    @State private var isSaving = false
    @State private var result: String?
    @State private var showError = false
    @State private var errorText = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("Measurement") {
                    TextField("Biomarker name", text: $biomarker)
                        .autocorrectionDisabled()
                    TextField("Value", text: $value)
                        .keyboardType(.decimalPad)
                    TextField("Unit (e.g. nmol/L, mg/dL)", text: $unit)
                        .autocorrectionDisabled()
                    DatePicker("Date", selection: $date, displayedComponents: .date)
                }

                if let result {
                    Section("Result") {
                        Text(result)
                            .foregroundStyle(.green)
                    }
                }
            }
            .navigationTitle("Add Measurement")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task { await save() }
                    }
                    .disabled(biomarker.isEmpty || value.isEmpty || unit.isEmpty || isSaving)
                }
            }
            .alert("Error", isPresented: $showError) {
                Button("OK") {}
            } message: {
                Text(errorText)
            }
        }
    }

    private func save() async {
        guard let numValue = Double(value) else {
            errorText = "Invalid value"
            showError = true
            return
        }

        isSaving = true
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd"

        do {
            let response = try await MCPClient.shared.addMeasurement(
                date: fmt.string(from: date),
                biomarker: biomarker,
                value: numValue,
                unit: unit
            )
            result = "Added \(response.matchedBiomarker ?? biomarker): \(numValue) \(unit)"
            await onSave()

            try? await Task.sleep(for: .seconds(1.5))
            dismiss()
        } catch {
            errorText = error.localizedDescription
            showError = true
        }
        isSaving = false
    }
}
