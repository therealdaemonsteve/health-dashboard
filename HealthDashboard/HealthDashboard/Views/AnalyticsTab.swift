import SwiftUI

@Observable
class AnalyticsViewModel {
    var trends: TrendDetectionResponse?
    var healthScores: HealthScoresResponse?
    var isLoading = false
    var error: String?

    var trendingUp: [BiomarkerTrend] {
        (trends?.trends ?? []).filter { $0.direction == "rising" && $0.alertLevel != "stable" }
    }

    var trendingDown: [BiomarkerTrend] {
        (trends?.trends ?? []).filter { $0.direction == "falling" && $0.alertLevel != "stable" }
    }

    func load() async {
        isLoading = true
        error = nil
        do {
            async let t = MCPClient.shared.detectTrends()
            async let s = MCPClient.shared.getHealthScores()
            let (trendsResult, scoresResult) = try await (t, s)
            trends = trendsResult
            healthScores = scoresResult
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }
}

struct AnalyticsTab: View {
    @State private var vm = AnalyticsViewModel()

    var body: some View {
        NavigationStack {
            ScrollView {
                if vm.isLoading && vm.trends == nil {
                    ProgressView("Loading...")
                        .padding(.top, 60)
                } else if let error = vm.error, vm.trends == nil {
                    ContentUnavailableView("Error", systemImage: "exclamationmark.triangle", description: Text(error))
                } else {
                    content
                }
            }
            .navigationTitle("Analytics")
            .refreshable { await vm.load() }
            .task { await vm.load() }
        }
    }

    @ViewBuilder
    private var content: some View {
        VStack(alignment: .leading, spacing: 24) {
            // Trend Summary
            if let summary = vm.trends?.summary {
                TrendSummaryCard(summary: summary)
            }

            // Analysis Tools
            VStack(alignment: .leading, spacing: 8) {
                Text("Analysis Tools")
                    .font(.headline)
                    .padding(.leading, 4)

                NavigationLink(destination: CorrelationView()) {
                    AnalyticsToolCard(
                        icon: "chart.xyaxis.line",
                        color: .purple,
                        title: "Correlation Analysis",
                        description: "Compare two biomarkers to find statistical relationships, with lag analysis"
                    )
                }
                .buttonStyle(.plain)

                NavigationLink(destination: EventImpactView()) {
                    AnalyticsToolCard(
                        icon: "waveform.path.ecg",
                        color: .blue,
                        title: "Event Impact",
                        description: "Measure how supplement changes, TRT doses, and other events affected biomarkers"
                    )
                }
                .buttonStyle(.plain)
            }

            // Active Trends
            if !vm.trendingUp.isEmpty || !vm.trendingDown.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Active Trends")
                        .font(.headline)
                        .padding(.leading, 4)

                    if !vm.trendingUp.isEmpty {
                        TrendGroup(label: "Rising", icon: "arrow.up.right", color: .red, trends: vm.trendingUp)
                    }
                    if !vm.trendingDown.isEmpty {
                        TrendGroup(label: "Falling", icon: "arrow.down.right", color: .blue, trends: vm.trendingDown)
                    }
                }
            }

            // Category Scores
            if let scores = vm.healthScores {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Category Scores")
                        .font(.headline)
                        .padding(.leading, 4)

                    ForEach(scores.categories) { cat in
                        CategoryScoreRow(category: cat)
                    }
                }
            }
        }
        .padding()
    }
}

// MARK: - Trend Summary Card

private struct TrendSummaryCard: View {
    let summary: TrendSummary

    var body: some View {
        HStack(spacing: 0) {
            SummaryItem(count: summary.critical, label: "Critical", color: .red)
            Divider().frame(height: 30)
            SummaryItem(count: summary.warning, label: "Warning", color: .orange)
            Divider().frame(height: 30)
            SummaryItem(count: summary.watch, label: "Watch", color: .yellow)
            Divider().frame(height: 30)
            SummaryItem(count: summary.stable, label: "Stable", color: .green)
        }
        .padding(.vertical, 14)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .shadow(color: .black.opacity(0.06), radius: 4, y: 2)
    }
}

private struct SummaryItem: View {
    let count: Int
    let label: String
    let color: Color

    var body: some View {
        VStack(spacing: 4) {
            Text("\(count)")
                .font(.title2)
                .fontWeight(.bold)
                .foregroundStyle(count > 0 ? color : .secondary)
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }
}

// MARK: - Analytics Tool Card

private struct AnalyticsToolCard: View {
    let icon: String
    let color: Color
    let title: String
    let description: String

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: icon)
                .font(.title2)
                .foregroundStyle(color)
                .frame(width: 40, height: 40)
                .background(color.opacity(0.1))
                .clipShape(RoundedRectangle(cornerRadius: 10))

            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .fontWeight(.semibold)
                    .font(.subheadline)
                Text(description)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            Spacer()

            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .padding(14)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
    }
}

// MARK: - Trend Group

private struct TrendGroup: View {
    let label: String
    let icon: String
    let color: Color
    let trends: [BiomarkerTrend]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 4) {
                Image(systemName: icon)
                    .font(.caption)
                    .foregroundStyle(color)
                Text(label)
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundStyle(color)
            }
            .padding(.leading, 4)

            ForEach(trends) { trend in
                NavigationLink(destination: BiomarkerDetailView(biomarkerName: trend.biomarker)) {
                    HStack {
                        Text(trend.biomarker)
                            .font(.subheadline)
                            .fontWeight(.medium)

                        Spacer()

                        if let value = trend.currentValue, let unit = trend.unit {
                            Text("\(String(format: "%.1f", value)) \(unit)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .monospacedDigit()
                        }

                        if let level = trend.alertLevel {
                            Text(level.capitalized)
                                .font(.system(size: 9, weight: .semibold))
                                .padding(.horizontal, 5)
                                .padding(.vertical, 2)
                                .background(alertColor(level).opacity(0.12))
                                .foregroundStyle(alertColor(level))
                                .clipShape(Capsule())
                        }

                        Image(systemName: "chevron.right")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                    .padding(10)
                    .background(.background)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .shadow(color: .black.opacity(0.03), radius: 1, y: 1)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func alertColor(_ level: String) -> Color {
        switch level {
        case "critical": return .red
        case "warning": return .orange
        case "watch": return .yellow
        default: return .green
        }
    }
}

// MARK: - Category Score Row

private struct CategoryScoreRow: View {
    let category: CategoryScore

    var body: some View {
        HStack(spacing: 12) {
            // Mini gauge
            ZStack {
                Circle()
                    .stroke(.quaternary, lineWidth: 3)
                    .frame(width: 36, height: 36)
                Circle()
                    .trim(from: 0, to: category.score / 100)
                    .stroke(gradeColor, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                    .frame(width: 36, height: 36)
                    .rotationEffect(.degrees(-90))
                Text(category.grade)
                    .font(.caption2)
                    .fontWeight(.bold)
                    .foregroundStyle(gradeColor)
            }

            VStack(alignment: .leading, spacing: 2) {
                Text(category.category)
                    .font(.subheadline)
                    .fontWeight(.medium)
                Text("\(category.biomarkerCount) biomarkers")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            Text(String(format: "%.0f", category.score))
                .font(.subheadline)
                .fontWeight(.semibold)
                .monospacedDigit()
                .foregroundStyle(gradeColor)
        }
        .padding(12)
        .background(.background)
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
    }

    private var gradeColor: Color {
        switch category.grade {
        case "A": return .green
        case "B": return .blue
        case "C": return .orange
        case "D": return .red
        default: return .secondary
        }
    }
}
