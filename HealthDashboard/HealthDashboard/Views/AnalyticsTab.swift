import SwiftUI

struct AnalyticsTab: View {
    var body: some View {
        NavigationStack {
            List {
                NavigationLink(destination: CorrelationView()) {
                    Label {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Correlation Analysis")
                                .fontWeight(.medium)
                            Text("Compare two biomarkers to find relationships")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    } icon: {
                        Image(systemName: "chart.xyaxis.line")
                            .foregroundStyle(.purple)
                    }
                }

                NavigationLink(destination: EventImpactView()) {
                    Label {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Event Impact")
                                .fontWeight(.medium)
                            Text("See how events affected your biomarkers")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    } icon: {
                        Image(systemName: "waveform.path.ecg")
                            .foregroundStyle(.blue)
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Analytics")
        }
    }
}
