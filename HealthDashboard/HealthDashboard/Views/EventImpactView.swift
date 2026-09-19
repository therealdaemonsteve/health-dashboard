import SwiftUI

@Observable
class EventImpactViewModel {
    var events: [HealthEvent] = []
    var result: EventImpactResponse?
    var isLoadingEvents = false
    var isAnalysing = false
    var error: String?

    func loadEvents() async {
        isLoadingEvents = true
        do {
            events = try await MCPClient.shared.getEvents()
        } catch {
            self.error = error.localizedDescription
        }
        isLoadingEvents = false
    }

    func analyse(eventId: String, windowDays: Int) async {
        isAnalysing = true
        error = nil
        result = nil
        do {
            result = try await MCPClient.shared.analyseEventImpact(eventId: eventId, windowDays: windowDays)
        } catch {
            self.error = error.localizedDescription
        }
        isAnalysing = false
    }
}

struct EventImpactView: View {
    @State private var vm = EventImpactViewModel()
    @State private var selectedEventId = ""
    @State private var windowDays = 60

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                // Event picker
                VStack(alignment: .leading, spacing: 12) {
                    Text("Select Event")
                        .font(.headline)

                    if vm.isLoadingEvents {
                        ProgressView()
                    } else {
                        Picker("Event", selection: $selectedEventId) {
                            Text("Select an event...").tag("")
                            ForEach(vm.events) { event in
                                Text("\(event.date) — \(event.title)")
                                    .tag(event.id)
                            }
                        }
                        .pickerStyle(.menu)

                        Picker("Window", selection: $windowDays) {
                            Text("30 days").tag(30)
                            Text("60 days").tag(60)
                            Text("90 days").tag(90)
                        }
                        .pickerStyle(.segmented)
                    }

                    Button {
                        Task { await vm.analyse(eventId: selectedEventId, windowDays: windowDays) }
                    } label: {
                        if vm.isAnalysing {
                            ProgressView()
                                .frame(maxWidth: .infinity)
                        } else {
                            Text("Analyse Impact")
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(selectedEventId.isEmpty || vm.isAnalysing)
                }

                if let error = vm.error {
                    Text(error)
                        .font(.callout)
                        .foregroundStyle(.red)
                }

                // Results
                if let r = vm.result {
                    resultsSection(r)
                }
            }
            .padding()
        }
        .navigationTitle("Event Impact")
        .navigationBarTitleDisplayMode(.large)
        .task { await vm.loadEvents() }
    }

    @ViewBuilder
    private func resultsSection(_ r: EventImpactResponse) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            // Event header
            VStack(alignment: .leading, spacing: 4) {
                Text(r.event.title)
                    .font(.headline)
                HStack {
                    Text(r.event.date)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("±\(r.windowDays) day window")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }

            if r.biomarkerImpacts.isEmpty {
                Text("No biomarkers with data in both the before and after periods.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .padding()
            }

            // Impact cards
            ForEach(r.biomarkerImpacts) { impact in
                ImpactCard(impact: impact)
            }
        }
    }
}

private struct ImpactCard: View {
    let impact: BiomarkerImpact

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(impact.biomarker)
                    .fontWeight(.medium)
                if let unit = impact.unit {
                    Text(unit)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if impact.likelySignificant == true {
                    Text("Significant")
                        .font(.caption2)
                        .fontWeight(.semibold)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(.green.opacity(0.15))
                        .foregroundStyle(.green)
                        .clipShape(Capsule())
                }
            }

            HStack(spacing: 16) {
                // Before
                VStack(spacing: 2) {
                    Text("Before")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    if let mean = impact.before.mean {
                        Text(String(format: "%.1f", mean))
                            .font(.callout)
                            .fontWeight(.semibold)
                            .monospacedDigit()
                    } else {
                        Text("-")
                            .font(.callout)
                    }
                    Text("n=\(impact.before.n ?? 0)")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                .frame(maxWidth: .infinity)

                // Arrow with change
                VStack(spacing: 2) {
                    Image(systemName: impact.direction == "increased" ? "arrow.right" : impact.direction == "decreased" ? "arrow.right" : "equal")
                        .foregroundStyle(changeColor)
                    if let pct = impact.changePct {
                        Text(String(format: "%+.1f%%", pct))
                            .font(.caption)
                            .fontWeight(.bold)
                            .foregroundStyle(changeColor)
                    }
                }

                // After
                VStack(spacing: 2) {
                    Text("After")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    if let mean = impact.after.mean {
                        Text(String(format: "%.1f", mean))
                            .font(.callout)
                            .fontWeight(.semibold)
                            .monospacedDigit()
                    } else {
                        Text("-")
                            .font(.callout)
                    }
                    Text("n=\(impact.after.n ?? 0)")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
                .frame(maxWidth: .infinity)
            }
        }
        .padding(12)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
    }

    private var changeColor: Color {
        guard let pct = impact.changePct else { return .secondary }
        if abs(pct) < 5 { return .secondary }
        return pct > 0 ? .red : .green
    }
}
