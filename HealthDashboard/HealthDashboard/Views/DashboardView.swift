import SwiftUI

struct DashboardView: View {
    @Binding var isAuthenticated: Bool

    var body: some View {
        TabView {
            OverviewTab()
                .tabItem {
                    Label("Overview", systemImage: "heart.text.clipboard")
                }

            BiomarkersTab()
                .tabItem {
                    Label("Biomarkers", systemImage: "list.bullet.clipboard")
                }

            HealthSyncTab()
                .tabItem {
                    Label("Sync", systemImage: "arrow.triangle.2.circlepath")
                }

            AnalyticsTab()
                .tabItem {
                    Label("Analytics", systemImage: "chart.xyaxis.line")
                }

            CoachingTab()
                .tabItem {
                    Label("Coaching", systemImage: "target")
                }

            PhasesTab()
                .tabItem {
                    Label("Phases", systemImage: "flag")
                }

            MoreTab(isAuthenticated: $isAuthenticated)
                .tabItem {
                    Label("More", systemImage: "ellipsis.circle")
                }
        }
    }
}
