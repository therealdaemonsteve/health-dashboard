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
            // Secondary: larger absolute percent change = more urgent
            return abs(a.pctChange ?? 0) > abs(b.pctChange ?? 0)
        }
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

        // Calculate how far outside the optimal range the value is
        var deviation: Double = 0
        if let green = ref.green, green.count == 2 {
            let midpoint = (green[0] + green[1]) / 2
            let halfRange = (green[1] - green[0]) / 2
            if halfRange > 0 {
                deviation = abs(value - midpoint) / halfRange // normalized deviation
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
        VStack(alignment: .leading, spacing: 20) {
            // Health Score
            if let scores = vm.healthScores {
                HealthScoreSection(scores: scores)
            }

            // Headline
            if let headline = vm.overview?.headline {
                Text(headline)
                    .font(.body)
                    .padding()
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.blue.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }

            // Status counts
            if let counts = vm.overview?.statusCounts {
                HStack(spacing: 12) {
                    StatusCountCard(label: "Green", count: counts.green, color: .green)
                    StatusCountCard(label: "Amber", count: counts.amber, color: .orange)
                    StatusCountCard(label: "Red", count: counts.red, color: .red)
                }
            }

            // Trend Alerts
            if !vm.alertTrends.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Trend Alerts")
                        .font(.headline)

                    ForEach(vm.alertTrends) { trend in
                        NavigationLink(destination: BiomarkerDetailView(biomarkerName: trend.biomarker)) {
                            TrendAlertRow(trend: trend)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            // Flagged biomarkers (sorted by severity)
            if !vm.flagged.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Flagged Biomarkers")
                        .font(.headline)

                    ForEach(vm.sortedFlagged) { item in
                        NavigationLink(destination: BiomarkerDetailView(biomarkerName: item.name)) {
                            FlaggedRow(item: item)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            // Categories
            if let categories = vm.overview?.categories, !categories.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Categories")
                        .font(.headline)

                    ForEach(categories) { cat in
                        CategoryCard(category: cat)
                    }
                }
            }

            // Recommendations
            if let recs = vm.overview?.recommendations, !recs.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Recommendations")
                        .font(.headline)

                    ForEach(recs) { rec in
                        RecommendationCard(recommendation: rec)
                    }
                }
            }
        }
        .padding()
    }
}

// MARK: - Subviews

private struct StatusCountCard: View {
    let label: String
    let count: Int
    let color: Color

    var body: some View {
        VStack(spacing: 4) {
            Text("\(count)")
                .font(.title2)
                .fontWeight(.bold)
                .foregroundStyle(color)
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 12)
        .background(color.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

private struct FlaggedRow: View {
    let item: FlaggedBiomarker

    var body: some View {
        HStack {
            StatusDot(status: item.status)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.name)
                    .fontWeight(.medium)
                if let value = item.latestValue, let unit = item.unit {
                    Text("\(value, specifier: "%.1f") \(unit)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
            if let pct = item.pctChange {
                Text("\(pct >= 0 ? "+" : "")\(pct, specifier: "%.0f")%")
                    .font(.caption)
                    .foregroundStyle(pct >= 0 ? .red : .green)
            }
            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .padding(12)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
    }
}

private struct CategoryCard: View {
    let category: OverviewCategory

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(category.displayName)
                    .fontWeight(.medium)
                Spacer()
                toneIndicator
            }
            Text(category.displayText)
                .font(.callout)
                .foregroundStyle(.secondary)
                .lineLimit(3)
        }
        .padding(12)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
    }

    @ViewBuilder
    private var toneIndicator: some View {
        switch category.displayTone {
        case "positive", "green":
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(.green)
        case "watch", "amber", "yellow":
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
        case "red", "critical":
            Image(systemName: "xmark.circle.fill")
                .foregroundStyle(.red)
        default:
            Image(systemName: "minus.circle.fill")
                .foregroundStyle(.secondary)
        }
    }
}

private struct HealthScoreSection: View {
    let scores: HealthScoresResponse
    @State private var expandedCategory: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Overall score gauge
            HStack(spacing: 16) {
                ZStack {
                    Circle()
                        .stroke(.quaternary, lineWidth: 8)
                        .frame(width: 70, height: 70)
                    Circle()
                        .trim(from: 0, to: scores.overallScore / 100)
                        .stroke(gradeColor(scores.overallGrade), style: StrokeStyle(lineWidth: 8, lineCap: .round))
                        .frame(width: 70, height: 70)
                        .rotationEffect(.degrees(-90))
                    VStack(spacing: 0) {
                        Text(scores.overallGrade)
                            .font(.title2)
                            .fontWeight(.bold)
                        Text(String(format: "%.0f", scores.overallScore))
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }

                VStack(alignment: .leading, spacing: 4) {
                    Text("Health Score")
                        .font(.headline)
                    Text("\(scores.categories.count) categories scored")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }

            // Category bars
            ForEach(scores.categories) { cat in
                VStack(spacing: 4) {
                    Button {
                        withAnimation {
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
                            Image(systemName: "chevron.right")
                                .font(.caption2)
                                .foregroundStyle(.tertiary)
                                .rotationEffect(.degrees(expandedCategory == cat.category ? 90 : 0))
                        }
                    }
                    .buttonStyle(.plain)

                    GeometryReader { geo in
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 3)
                                .fill(.quaternary)
                                .frame(height: 6)
                            RoundedRectangle(cornerRadius: 3)
                                .fill(gradeColor(cat.grade))
                                .frame(width: geo.size.width * cat.score / 100, height: 6)
                        }
                    }
                    .frame(height: 6)

                    if expandedCategory == cat.category {
                        ForEach(cat.biomarkerScores) { bs in
                            HStack {
                                StatusDot(status: RAGStatus(rawValue: bs.status ?? "unknown") ?? .unknown)
                                Text(bs.biomarker)
                                    .font(.caption2)
                                Spacer()
                                if let v = bs.value {
                                    Text(String(format: "%.1f", v))
                                        .font(.caption2)
                                        .monospacedDigit()
                                }
                                Text(String(format: "%.0f", bs.score))
                                    .font(.caption2)
                                    .fontWeight(.semibold)
                                    .foregroundStyle(gradeColor(bs.score >= 90 ? "A" : bs.score >= 75 ? "B" : bs.score >= 60 ? "C" : "D"))
                            }
                            .padding(.leading, 8)
                        }
                    }
                }
            }
        }
        .padding()
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .shadow(color: .black.opacity(0.05), radius: 3, y: 2)
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

private struct TrendAlertRow: View {
    let trend: BiomarkerTrend

    var body: some View {
        HStack {
            Image(systemName: trend.alertLevel == "critical" ? "exclamationmark.triangle.fill" : "arrow.triangle.2.circlepath")
                .foregroundStyle(trend.alertLevel == "critical" ? .red : .orange)
                .frame(width: 24)

            VStack(alignment: .leading, spacing: 2) {
                Text(trend.biomarker)
                    .fontWeight(.medium)
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
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .padding(12)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
    }
}

private struct RecommendationCard: View {
    let recommendation: Recommendation

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            priorityIcon
            VStack(alignment: .leading, spacing: 4) {
                if let title = recommendation.title {
                    Text(title)
                        .fontWeight(.medium)
                    Text(recommendation.text)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                } else {
                    Text(recommendation.text)
                        .font(.callout)
                }
            }
        }
        .padding(12)
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
