import SwiftUI

@Observable
class OverviewViewModel {
    var overview: HealthOverview?
    var flagged: [FlaggedBiomarker] = []
    var healthScores: HealthScoresResponse?
    var trends: TrendDetectionResponse?
    var isLoading = false
    var error: String?

    /// Flagged biomarkers sorted by severity: red first, then amber.
    /// Within each status, sorted by deviation from optimal range (largest deviation first).
    var sortedFlagged: [FlaggedBiomarker] {
        flagged.sorted { a, b in
            let aScore = severityScore(a)
            let bScore = severityScore(b)
            if aScore != bScore { return aScore > bScore }
            return abs(a.pctChange ?? 0) > abs(b.pctChange ?? 0)
        }
    }

    var redFlagged: [FlaggedBiomarker] {
        sortedFlagged.filter { $0.status == .red }
    }

    var amberFlagged: [FlaggedBiomarker] {
        sortedFlagged.filter { $0.status == .amber }
    }

    /// Trends that need attention (critical + warning only)
    var alertTrends: [BiomarkerTrend] {
        (trends?.trends ?? []).filter { $0.alertLevel == "critical" || $0.alertLevel == "warning" }
    }

    func load() async {
        isLoading = true
        error = nil
        do {
            async let o = MCPClient.shared.getHealthOverview()
            async let f = MCPClient.shared.getFlaggedBiomarkers()
            async let s = MCPClient.shared.getHealthScores()
            async let t = MCPClient.shared.detectTrends()
            let (overviewResult, flaggedResult, scoresResult, trendsResult) = try await (o, f, s, t)
            overview = overviewResult
            flagged = flaggedResult
            healthScores = scoresResult
            trends = trendsResult
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }

    /// Score: red = 100 + deviation, amber = 0 + deviation
    private func severityScore(_ item: FlaggedBiomarker) -> Double {
        let base: Double = item.status == .red ? 100 : 0
        guard let value = item.latestValue, let ref = item.reference else {
            return base + abs(item.pctChange ?? 0)
        }

        var deviation: Double = 0
        if let green = ref.green, green.count == 2 {
            let midpoint = (green[0] + green[1]) / 2
            let halfRange = (green[1] - green[0]) / 2
            if halfRange > 0 {
                deviation = abs(value - midpoint) / halfRange
            }
        } else if let redLow = ref.redLow, value < redLow, redLow > 0 {
            deviation = (redLow - value) / redLow * 100
        } else if let redHigh = ref.redHigh, value > redHigh, redHigh > 0 {
            deviation = (value - redHigh) / redHigh * 100
        } else {
            deviation = abs(item.pctChange ?? 0)
        }

        return base + deviation
    }
}

struct OverviewTab: View {
    @State private var vm = OverviewViewModel()

    var body: some View {
        NavigationStack {
            ScrollView {
                if vm.isLoading && vm.overview == nil {
                    ProgressView("Loading...")
                        .padding(.top, 60)
                } else if let error = vm.error, vm.overview == nil {
                    ContentUnavailableView("Error", systemImage: "exclamationmark.triangle", description: Text(error))
                } else {
                    content
                }
            }
            .navigationTitle("Overview")
            .refreshable { await vm.load() }
            .task { await vm.load() }
        }
    }

    @ViewBuilder
    private var content: some View {
        VStack(alignment: .leading, spacing: 24) {
            // Hero: Health Score + Status Summary
            if let scores = vm.healthScores {
                HealthScoreHero(scores: scores, statusCounts: vm.overview?.statusCounts)
            }

            // AI Headline
            if let headline = vm.overview?.headline, !headline.isEmpty {
                HeadlineCard(text: headline)
            }

            // Trend Alerts
            if !vm.alertTrends.isEmpty {
                SectionHeader(title: "Trend Alerts", icon: "exclamationmark.triangle.fill", color: .orange)
                VStack(spacing: 8) {
                    ForEach(vm.alertTrends) { trend in
                        NavigationLink(destination: BiomarkerDetailView(biomarkerName: trend.biomarker)) {
                            TrendAlertRow(trend: trend)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            // Flagged: Red
            if !vm.redFlagged.isEmpty {
                SectionHeader(title: "Needs Attention", icon: "xmark.circle.fill", color: .red, count: vm.redFlagged.count)
                VStack(spacing: 8) {
                    ForEach(vm.redFlagged) { item in
                        NavigationLink(destination: BiomarkerDetailView(biomarkerName: item.name)) {
                            FlaggedRow(item: item)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            // Flagged: Amber
            if !vm.amberFlagged.isEmpty {
                SectionHeader(title: "Watch List", icon: "exclamationmark.triangle.fill", color: .orange, count: vm.amberFlagged.count)
                VStack(spacing: 8) {
                    ForEach(vm.amberFlagged) { item in
                        NavigationLink(destination: BiomarkerDetailView(biomarkerName: item.name)) {
                            FlaggedRow(item: item)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            // Category Scores
            if let scores = vm.healthScores, !scores.categories.isEmpty {
                SectionHeader(title: "Categories", icon: "square.grid.2x2.fill", color: .blue)
                CategoryScoreGrid(categories: scores.categories)
            }

            // Recommendations
            if let recs = vm.overview?.recommendations, !recs.isEmpty {
                SectionHeader(title: "Recommendations", icon: "lightbulb.fill", color: .yellow)
                VStack(spacing: 8) {
                    ForEach(recs) { rec in
                        RecommendationCard(recommendation: rec)
                    }
                }
            }
        }
        .padding()
    }
}

// MARK: - Section Header

private struct SectionHeader: View {
    let title: String
    let icon: String
    let color: Color
    var count: Int? = nil

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: icon)
                .font(.subheadline)
                .foregroundStyle(color)
            Text(title)
                .font(.headline)
            if let count {
                Text("\(count)")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 1)
                    .background(color.opacity(0.15))
                    .foregroundStyle(color)
                    .clipShape(Capsule())
            }
            Spacer()
        }
        .padding(.top, 4)
    }
}

// MARK: - Health Score Hero

private struct HealthScoreHero: View {
    let scores: HealthScoresResponse
    let statusCounts: StatusCounts?
    @State private var expandedCategory: String?

    var body: some View {
        VStack(spacing: 16) {
            // Top row: score gauge + summary
            HStack(spacing: 20) {
                // Large circular gauge
                ZStack {
                    Circle()
                        .stroke(.quaternary, lineWidth: 10)
                        .frame(width: 90, height: 90)
                    Circle()
                        .trim(from: 0, to: scores.overallScore / 100)
                        .stroke(gradeColor(scores.overallGrade), style: StrokeStyle(lineWidth: 10, lineCap: .round))
                        .frame(width: 90, height: 90)
                        .rotationEffect(.degrees(-90))
                    VStack(spacing: 0) {
                        Text(scores.overallGrade)
                            .font(.title)
                            .fontWeight(.bold)
                            .foregroundStyle(gradeColor(scores.overallGrade))
                        Text(String(format: "%.0f", scores.overallScore))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                // Status summary
                if let counts = statusCounts {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Health Score")
                            .font(.headline)
                        HStack(spacing: 12) {
                            StatusPill(count: counts.green, color: .green, label: "Optimal")
                            StatusPill(count: counts.amber, color: .orange, label: "Watch")
                            StatusPill(count: counts.red, color: .red, label: "Flag")
                        }
                    }
                } else {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Health Score")
                            .font(.headline)
                        Text("\(scores.categories.count) categories")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
            }

            // Category breakdown bars
            VStack(spacing: 6) {
                ForEach(scores.categories) { cat in
                    VStack(spacing: 3) {
                        Button {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                expandedCategory = expandedCategory == cat.category ? nil : cat.category
                            }
                        } label: {
                            HStack {
                                Text(cat.category)
                                    .font(.caption)
                                    .fontWeight(.medium)
                                Spacer()
                                Text(cat.grade)
                                    .font(.caption)
                                    .fontWeight(.bold)
                                    .foregroundStyle(gradeColor(cat.grade))
                                Text(String(format: "%.0f", cat.score))
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                    .frame(width: 24, alignment: .trailing)
                                Image(systemName: "chevron.right")
                                    .font(.system(size: 8, weight: .bold))
                                    .foregroundStyle(.tertiary)
                                    .rotationEffect(.degrees(expandedCategory == cat.category ? 90 : 0))
                            }
                        }
                        .buttonStyle(.plain)

                        GeometryReader { geo in
                            ZStack(alignment: .leading) {
                                RoundedRectangle(cornerRadius: 3)
                                    .fill(.quaternary)
                                    .frame(height: 5)
                                RoundedRectangle(cornerRadius: 3)
                                    .fill(gradeColor(cat.grade))
                                    .frame(width: geo.size.width * cat.score / 100, height: 5)
                            }
                        }
                        .frame(height: 5)

                        if expandedCategory == cat.category {
                            ForEach(cat.biomarkerScores) { bs in
                                HStack {
                                    StatusDot(status: RAGStatus(rawValue: bs.status ?? "unknown") ?? .unknown)
                                    Text(bs.biomarker)
                                        .font(.caption2)
                                    Spacer()
                                    if let v = bs.value, let u = bs.unit {
                                        Text(Formatters.value(v) + " " + u)
                                            .font(.caption2)
                                            .monospacedDigit()
                                            .foregroundStyle(.secondary)
                                    }
                                    Text(String(format: "%.0f", bs.score))
                                        .font(.caption2)
                                        .fontWeight(.semibold)
                                        .foregroundStyle(gradeColor(bs.score >= 90 ? "A" : bs.score >= 75 ? "B" : bs.score >= 60 ? "C" : "D"))
                                        .frame(width: 24, alignment: .trailing)
                                }
                                .padding(.leading, 12)
                            }
                        }
                    }
                }
            }
        }
        .padding()
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .shadow(color: .black.opacity(0.06), radius: 4, y: 2)
    }

    private func gradeColor(_ grade: String) -> Color {
        switch grade {
        case "A": return .green
        case "B": return .blue
        case "C": return .orange
        case "D": return .red
        default: return .secondary
        }
    }

}

private struct StatusPill: View {
    let count: Int
    let color: Color
    let label: String

    var body: some View {
        VStack(spacing: 2) {
            Text("\(count)")
                .font(.callout)
                .fontWeight(.bold)
                .foregroundStyle(color)
            Text(label)
                .font(.system(size: 9))
                .foregroundStyle(.secondary)
        }
        .frame(minWidth: 44)
        .padding(.vertical, 6)
        .padding(.horizontal, 4)
        .background(color.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

// MARK: - Headline Card

private struct HeadlineCard: View {
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "sparkles")
                .font(.subheadline)
                .foregroundStyle(.blue)
                .padding(.top, 2)
            Text(text)
                .font(.subheadline)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(.blue.opacity(0.06))
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .strokeBorder(.blue.opacity(0.12), lineWidth: 1)
                )
        )
    }
}

// MARK: - Category Score Grid

private struct CategoryScoreGrid: View {
    let categories: [CategoryScore]

    private let columns = [
        GridItem(.flexible(), spacing: 8),
        GridItem(.flexible(), spacing: 8),
    ]

    var body: some View {
        LazyVGrid(columns: columns, spacing: 8) {
            ForEach(categories) { cat in
                HStack(spacing: 8) {
                    // Mini gauge
                    ZStack {
                        Circle()
                            .stroke(.quaternary, lineWidth: 3)
                            .frame(width: 32, height: 32)
                        Circle()
                            .trim(from: 0, to: cat.score / 100)
                            .stroke(gradeColor(cat.grade), style: StrokeStyle(lineWidth: 3, lineCap: .round))
                            .frame(width: 32, height: 32)
                            .rotationEffect(.degrees(-90))
                        Text(cat.grade)
                            .font(.caption2)
                            .fontWeight(.bold)
                            .foregroundStyle(gradeColor(cat.grade))
                    }

                    VStack(alignment: .leading, spacing: 1) {
                        Text(cat.category)
                            .font(.caption)
                            .fontWeight(.medium)
                            .lineLimit(1)
                        Text("\(cat.biomarkerCount) markers")
                            .font(.system(size: 9))
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }
                .padding(10)
                .background(.background)
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
            }
        }
    }

    private func gradeColor(_ grade: String) -> Color {
        switch grade {
        case "A": return .green
        case "B": return .blue
        case "C": return .orange
        case "D": return .red
        default: return .secondary
        }
    }
}

// MARK: - Flagged Row

private struct FlaggedRow: View {
    let item: FlaggedBiomarker

    var body: some View {
        HStack {
            StatusDot(status: item.status)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.name)
                    .fontWeight(.medium)
                    .font(.subheadline)
                if let value = item.latestValue, let unit = item.unit {
                    Text(Formatters.valueWithUnit(value, unit: unit))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
            if let pct = item.pctChange {
                Text("\(pct >= 0 ? "+" : "")\(pct, specifier: "%.0f")%")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundStyle(pct >= 0 ? .red : .green)
            }
            Image(systemName: "chevron.right")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding(12)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
    }

}

// MARK: - Trend Alert Row

private struct TrendAlertRow: View {
    let trend: BiomarkerTrend

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: trend.alertLevel == "critical" ? "exclamationmark.triangle.fill" : "arrow.triangle.2.circlepath")
                .foregroundStyle(trend.alertLevel == "critical" ? .red : .orange)
                .frame(width: 24)

            VStack(alignment: .leading, spacing: 2) {
                Text(trend.biomarker)
                    .fontWeight(.medium)
                    .font(.subheadline)
                HStack(spacing: 4) {
                    if let dir = trend.direction {
                        Image(systemName: dir == "rising" ? "arrow.up.right" : "arrow.down.right")
                            .font(.caption2)
                    }
                    if let value = trend.currentValue, let unit = trend.unit {
                        Text("\(String(format: "%.1f", value)) \(unit)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            Spacer()

            VStack(alignment: .trailing, spacing: 2) {
                Text(trend.alertLevel?.capitalized ?? "")
                    .font(.caption2)
                    .fontWeight(.semibold)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background((trend.alertLevel == "critical" ? Color.red : Color.orange).opacity(0.15))
                    .foregroundStyle(trend.alertLevel == "critical" ? .red : .orange)
                    .clipShape(Capsule())
                if let days = trend.daysToRed ?? trend.daysToAmber {
                    Text("\(days)d to breach")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }

            Image(systemName: "chevron.right")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding(12)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
    }
}

// MARK: - Recommendation Card

private struct RecommendationCard: View {
    let recommendation: Recommendation

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            priorityIcon
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 4) {
                if let title = recommendation.title {
                    Text(title)
                        .fontWeight(.medium)
                        .font(.subheadline)
                    Text(recommendation.text)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Text(recommendation.text)
                        .font(.subheadline)
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
    }

    @ViewBuilder
    private var priorityIcon: some View {
        let p = recommendation.priority?.stringValue ?? "low"
        switch p {
        case "high":
            Image(systemName: "exclamationmark.circle.fill")
                .foregroundStyle(.red)
        case "medium":
            Image(systemName: "info.circle.fill")
                .foregroundStyle(.orange)
        default:
            Image(systemName: "lightbulb.fill")
                .foregroundStyle(.blue)
        }
    }
}
