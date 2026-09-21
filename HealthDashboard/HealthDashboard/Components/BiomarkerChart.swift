import SwiftUI
import Charts

// MARK: - Date Range Filter

enum ChartDateRange: String, CaseIterable {
    case threeMonths = "3M"
    case sixMonths = "6M"
    case oneYear = "1Y"
    case all = "All"

    var startDate: Date? {
        let cal = Calendar.current
        let now = Date()
        switch self {
        case .threeMonths: return cal.date(byAdding: .month, value: -3, to: now)
        case .sixMonths: return cal.date(byAdding: .month, value: -6, to: now)
        case .oneYear: return cal.date(byAdding: .year, value: -1, to: now)
        case .all: return nil
        }
    }
}

struct BiomarkerChart: View {
    let measurements: [Measurement]
    let reference: ReferenceRange?
    let unit: String?
    var dateRange: ChartDateRange = .all
    var rollingAverageData: [DataPoint]? = nil

    @State private var magnification: CGFloat = 1.0
    @State private var selectedDate: Date?
    @State private var scrollPosition = Date.now

    var body: some View {
        let data = filteredMeasurements
        Chart {
            // Reference range bands
            if let reference {
                if let green = reference.green, green.count == 2 {
                    RectangleMark(
                        yStart: .value("Low", green[0]),
                        yEnd: .value("High", green[1])
                    )
                    .foregroundStyle(.green.opacity(0.08))
                }

                if let amber = reference.amber {
                    ForEach(Array(amber.enumerated()), id: \.offset) { _, range in
                        if range.count == 2 {
                            RectangleMark(
                                yStart: .value("Low", range[0]),
                                yEnd: .value("High", range[1])
                            )
                            .foregroundStyle(.orange.opacity(0.08))
                        }
                    }
                }
            }

            // Data line
            ForEach(data, id: \.date) { m in
                if let date = Formatters.parseDate(m.date) {
                    LineMark(
                        x: .value("Date", date),
                        y: .value("Value", m.value)
                    )
                    .foregroundStyle(.blue)
                    .interpolationMethod(.catmullRom)

                    PointMark(
                        x: .value("Date", date),
                        y: .value("Value", m.value)
                    )
                    .foregroundStyle(pointColor(for: m))
                    .symbolSize(30)
                }
            }

            // Rolling average overlay
            if let rollingData = rollingAverageData {
                ForEach(rollingData) { pt in
                    if let date = Formatters.parseDate(pt.date) {
                        LineMark(
                            x: .value("Date", date),
                            y: .value("Rolling Avg", pt.value)
                        )
                        .foregroundStyle(.orange.opacity(0.8))
                        .lineStyle(StrokeStyle(lineWidth: 2, dash: [6, 3]))
                        .interpolationMethod(.catmullRom)
                    }
                }
            }

            // Selected date indicator
            if let selectedDate {
                RuleMark(x: .value("Selected", selectedDate))
                    .foregroundStyle(.secondary.opacity(0.5))
                    .lineStyle(StrokeStyle(dash: [4, 4]))
            }
        }
        .chartYAxisLabel(unit ?? "")
        .chartYScale(domain: yDomain(for: data))
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: 5)) { _ in
                AxisGridLine()
                AxisValueLabel(format: .dateTime.month(.abbreviated).year(.twoDigits))
            }
        }
        .chartXSelection(value: $selectedDate)
        .chartScrollableAxes(.horizontal)
        .chartScrollPosition(x: $scrollPosition)
        .frame(height: 220)
        .onAppear {
            if let lastDate = data.last.flatMap({ Formatters.parseDate($0.date) }) {
                scrollPosition = lastDate
            }
        }
        .overlay(alignment: .topLeading) {
            selectedValueOverlay(data: data)
        }
        .gesture(
            MagnifyGesture()
                .onChanged { value in
                    magnification = value.magnification
                }
        )
    }

    @ViewBuilder
    private func selectedValueOverlay(data: [Measurement]) -> some View {
        if let selectedDate,
           let closest = closestMeasurement(to: selectedDate, in: data) {
            VStack(alignment: .leading, spacing: 2) {
                Text(closest.date)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Text(Formatters.valueWithUnit(closest.value, unit: closest.unit))
                    .font(.caption)
                    .fontWeight(.semibold)
            }
            .padding(6)
            .background(.ultraThinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: 6))
            .padding(4)
        }
    }

    private func closestMeasurement(to date: Date, in data: [Measurement]) -> Measurement? {
        data.min(by: {
            guard let d1 = Formatters.parseDate($0.date), let d2 = Formatters.parseDate($1.date) else { return false }
            return abs(d1.timeIntervalSince(date)) < abs(d2.timeIntervalSince(date))
        })
    }

    private func yDomain(for data: [Measurement]) -> ClosedRange<Double> {
        var lo = data.map(\.value).min() ?? 0
        var hi = data.map(\.value).max() ?? 1

        if let ref = reference {
            if let green = ref.green, green.count == 2 {
                lo = min(lo, green[0])
                hi = max(hi, green[1])
            }
            if let amber = ref.amber {
                for range in amber where range.count == 2 {
                    lo = min(lo, range[0])
                    hi = max(hi, range[1])
                }
            }
            if let redLow = ref.redLow { lo = min(lo, redLow) }
            if let redHigh = ref.redHigh { hi = max(hi, redHigh) }
        }

        let padding = (hi - lo) * 0.05
        return (lo - padding) ... (hi + padding)
    }

    private var filteredMeasurements: [Measurement] {
        let sorted = measurements
            .filter { !($0.flaggedErroneous ?? false) }
            .sorted { $0.date < $1.date }

        guard let cutoff = dateRange.startDate else { return sorted }
        return sorted.filter {
            guard let d = Formatters.parseDate($0.date) else { return true }
            return d >= cutoff
        }
    }

    private func pointColor(for m: Measurement) -> Color {
        switch m.rag ?? m.status {
        case .green: return .green
        case .amber: return .orange
        case .red: return .red
        default: return .blue
        }
    }
}
