import SwiftUI
import Charts

@Observable
class BiomarkerDetailViewModel {
    var detail: BiomarkerDetail?
    var measurements: [Measurement] = []
    var rollingAverageData: [DataPoint]?
    var isLoading = false
    var error: String?
    var deleteTarget: Measurement?
    var isRegeneratingInsight = false

    func load(name: String) async {
        isLoading = true
        error = nil
        do {
            async let d = MCPClient.shared.getBiomarkerDetail(name: name)
            async let m = MCPClient.shared.getMeasurements(biomarker: name)
            let (detailResult, measurementsResult) = try await (d, m)
            detail = detailResult
            measurements = measurementsResult.measurements
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }

    func loadRollingAverage(name: String) async {
        do {
            let response = try await MCPClient.shared.getRollingAverages(biomarker: name, windowDays: 14)
            rollingAverageData = response.rollingAverage
        } catch {
            rollingAverageData = nil
        }
    }

    func clearRollingAverage() {
        rollingAverageData = nil
    }

    func regenerateInsight(name: String) async {
        isRegeneratingInsight = true
        do {
            _ = try await MCPClient.shared.generateInsights(biomarker: name)
            // Reload to get the updated insight
            let updated = try await MCPClient.shared.getBiomarkerDetail(name: name)
            detail = updated
        } catch {
            self.error = error.localizedDescription
        }
        isRegeneratingInsight = false
    }

    func deleteMeasurement(_ measurement: Measurement, biomarkerName: String) async {
        guard let mid = measurement.measurementId else { return }
        do {
            _ = try await MCPClient.shared.deleteMeasurement(measurementId: mid)
            measurements.removeAll { $0.measurementId == mid }
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct BiomarkerDetailView: View {
    let biomarkerName: String
    @State private var vm = BiomarkerDetailViewModel()
    @State private var selectedDateRange: ChartDateRange = .sixMonths
    @State private var showRollingAverage = false

    var body: some View {
        ScrollView {
            if vm.isLoading && vm.detail == nil {
                ProgressView("Loading...")
                    .padding(.top, 60)
            } else if let error = vm.error, vm.detail == nil {
                ContentUnavailableView("Error", systemImage: "exclamationmark.triangle", description: Text(error))
            } else if let detail = vm.detail {
                content(detail)
            }
        }
        .navigationTitle(biomarkerName)
        .navigationBarTitleDisplayMode(.large)
        .refreshable { await vm.load(name: biomarkerName) }
        .task { await vm.load(name: biomarkerName) }
        .alert("Delete Measurement", isPresented: Binding(
            get: { vm.deleteTarget != nil },
            set: { if !$0 { vm.deleteTarget = nil } }
        )) {
            Button("Delete", role: .destructive) {
                if let target = vm.deleteTarget {
                    Task { await vm.deleteMeasurement(target, biomarkerName: biomarkerName) }
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            if let target = vm.deleteTarget {
                Text("Delete \(formatValue(target.value)) \(target.unit) from \(target.date)?")
            }
        }
    }

    @ViewBuilder
    private func content(_ detail: BiomarkerDetail) -> some View {
        VStack(alignment: .leading, spacing: 20) {
            // Header with current value
            headerSection(detail)

            // Chart
            if !vm.measurements.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text("Trend")
                            .font(.headline)
                        Spacer()
                        Picker("Range", selection: $selectedDateRange) {
                            ForEach(ChartDateRange.allCases, id: \.self) { range in
                                Text(range.rawValue).tag(range)
                            }
                        }
                        .pickerStyle(.segmented)
                        .frame(width: 200)
                    }
                    BiomarkerChart(
                        measurements: vm.measurements,
                        reference: detail.reference,
                        unit: detail.units?.first ?? detail.reference?.unit,
                        dateRange: selectedDateRange,
                        rollingAverageData: showRollingAverage ? vm.rollingAverageData : nil
                    )
                    Toggle("Rolling Average (14d)", isOn: $showRollingAverage)
                        .font(.caption)
                        .onChange(of: showRollingAverage) {
                            if showRollingAverage && vm.rollingAverageData == nil {
                                Task { await vm.loadRollingAverage(name: biomarkerName) }
                            } else if !showRollingAverage {
                                vm.clearRollingAverage()
                            }
                        }
                }
            }

            // Stats
            if let stats = detail.stats {
                statsSection(stats, unit: detail.units?.first ?? detail.reference?.unit)
            }

            // Reference ranges
            if let ref = detail.reference {
                referenceSection(ref)
            }

            // Insight
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Insight")
                        .font(.headline)
                    Spacer()
                    Button {
                        Task { await vm.regenerateInsight(name: biomarkerName) }
                    } label: {
                        if vm.isRegeneratingInsight {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            Label("Regenerate", systemImage: "sparkles")
                                .font(.caption)
                        }
                    }
                    .disabled(vm.isRegeneratingInsight)
                }
                if let insight = detail.insight, !insight.isEmpty {
                    Text(insight)
                        .font(.body)
                        .foregroundStyle(.secondary)
                } else {
                    Text("No insight available. Tap Regenerate to create one.")
                        .font(.callout)
                        .foregroundStyle(.tertiary)
                }
            }

            // Measurements table
            if !vm.measurements.isEmpty {
                measurementsTable
            }
        }
        .padding()
    }

    private func headerSection(_ detail: BiomarkerDetail) -> some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                if let stats = detail.stats, let value = stats.latestValue {
                    Text(formatValue(value))
                        .font(.system(size: 40, weight: .bold, design: .rounded))
                    if let unit = detail.units?.first ?? detail.reference?.unit {
                        Text(unit)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }
                }
                if let cat = detail.category {
                    Text(cat)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
            StatusBadge(status: detail.status)
        }
    }

    private func statsSection(_ stats: BiomarkerStats, unit: String?) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Statistics")
                .font(.headline)

            LazyVGrid(columns: [
                GridItem(.flexible()),
                GridItem(.flexible()),
                GridItem(.flexible()),
            ], spacing: 12) {
                StatBox(label: "Min", value: formatValue(stats.min))
                StatBox(label: "Mean", value: formatValue(stats.mean))
                StatBox(label: "Max", value: formatValue(stats.max))
                StatBox(label: "Measurements", value: stats.n.map { "\($0)" })
                StatBox(label: "Change", value: stats.pctChange.map { String(format: "%+.0f%%", $0) })
                StatBox(label: "Span", value: stats.spanDays.map { "\($0)d" })
            }
        }
    }

    private func referenceSection(_ ref: ReferenceRange) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Reference Range")
                .font(.headline)

            if let green = ref.green, green.count == 2 {
                HStack {
                    Circle().fill(.green).frame(width: 8, height: 8)
                    Text("Optimal: \(formatValue(green[0])) - \(formatValue(green[1]))")
                        .font(.callout)
                    if let unit = ref.unit {
                        Text(unit)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            if let amber = ref.amber {
                ForEach(Array(amber.enumerated()), id: \.offset) { _, range in
                    if range.count == 2 {
                        HStack {
                            Circle().fill(.orange).frame(width: 8, height: 8)
                            Text("Watch: \(formatValue(range[0])) - \(formatValue(range[1]))")
                                .font(.callout)
                        }
                    }
                }
            }

            if let low = ref.redLow {
                HStack {
                    Circle().fill(.red).frame(width: 8, height: 8)
                    Text("Low threshold: < \(formatValue(low))")
                        .font(.callout)
                }
            }
            if let high = ref.redHigh {
                HStack {
                    Circle().fill(.red).frame(width: 8, height: 8)
                    Text("High threshold: > \(formatValue(high))")
                        .font(.callout)
                }
            }
        }
    }

    private var measurementsTable: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("History")
                .font(.headline)

            ForEach(vm.measurements.sorted(by: { $0.date > $1.date })) { m in
                HStack {
                    StatusDot(status: m.rag ?? m.status)
                    Text(m.date)
                        .font(.callout)
                        .monospacedDigit()
                    Spacer()
                    Text("\(formatValue(m.value)) \(m.unit)")
                        .font(.callout)
                        .monospacedDigit()
                    if let source = m.sourceLabel ?? m.source {
                        Text(source)
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                    if m.measurementId != nil {
                        Button {
                            vm.deleteTarget = m
                        } label: {
                            Image(systemName: "trash")
                                .font(.caption)
                                .foregroundStyle(.red.opacity(0.6))
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.vertical, 2)
                if m.flaggedErroneous == true {
                    Text("Flagged as erroneous")
                        .font(.caption2)
                        .foregroundStyle(.red)
                }
            }
        }
    }

    private func formatValue(_ v: Double?) -> String {
        guard let v else { return "-" }
        if v == v.rounded() && abs(v) < 10000 {
            return String(format: "%.0f", v)
        } else if abs(v) < 1 {
            return String(format: "%.3f", v)
        }
        return String(format: "%.1f", v)
    }
}

private struct StatBox: View {
    let label: String
    let value: String?

    var body: some View {
        VStack(spacing: 2) {
            Text(value ?? "-")
                .font(.callout)
                .fontWeight(.semibold)
                .monospacedDigit()
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(.quaternary.opacity(0.5))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}
