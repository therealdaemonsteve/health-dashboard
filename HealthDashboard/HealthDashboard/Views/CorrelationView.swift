import SwiftUI

@Observable
class CorrelationViewModel {
    var biomarkers: [BiomarkerSummary] = []
    var result: CorrelationResponse?
    var isLoading = false
    var isLoadingBiomarkers = false
    var error: String?

    func loadBiomarkers() async {
        isLoadingBiomarkers = true
        do {
            biomarkers = try await MCPClient.shared.listBiomarkers()
        } catch {
            self.error = error.localizedDescription
        }
        isLoadingBiomarkers = false
    }

    func compute(a: String, b: String) async {
        isLoading = true
        error = nil
        result = nil
        do {
            result = try await MCPClient.shared.computeCorrelation(biomarkerA: a, biomarkerB: b)
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }
}

struct CorrelationView: View {
    @State private var vm = CorrelationViewModel()
    @State private var selectedA = ""
    @State private var selectedB = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                // Biomarker pickers
                VStack(alignment: .leading, spacing: 12) {
                    Text("Select Biomarkers")
                        .font(.headline)

                    if vm.isLoadingBiomarkers {
                        ProgressView()
                    } else {
                        biomarkerPicker(label: "Biomarker A", selection: $selectedA)
                        biomarkerPicker(label: "Biomarker B", selection: $selectedB)
                    }

                    Button {
                        Task { await vm.compute(a: selectedA, b: selectedB) }
                    } label: {
                        if vm.isLoading {
                            ProgressView()
                                .frame(maxWidth: .infinity)
                        } else {
                            Text("Compute Correlation")
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(selectedA.isEmpty || selectedB.isEmpty || selectedA == selectedB || vm.isLoading)
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
        .navigationTitle("Correlation")
        .navigationBarTitleDisplayMode(.large)
        .task { await vm.loadBiomarkers() }
    }

    private func biomarkerPicker(label: String, selection: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            Picker(label, selection: selection) {
                Text("Select...").tag("")
                ForEach(vm.biomarkers) { b in
                    Text(b.name).tag(b.name)
                }
            }
            .pickerStyle(.menu)
        }
    }

    @ViewBuilder
    private func resultsSection(_ r: CorrelationResponse) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            // Summary
            VStack(alignment: .leading, spacing: 8) {
                Text("Results")
                    .font(.headline)
                Text("\(r.biomarkerA) vs \(r.biomarkerB)")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                Text("\(r.nAligned) aligned data points")
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }

            // Correlation values
            LazyVGrid(columns: [
                GridItem(.flexible()),
                GridItem(.flexible()),
            ], spacing: 12) {
                CorrelationStatBox(
                    label: "Pearson r",
                    value: String(format: "%.3f", r.pearson.r),
                    significance: significanceStars(r.pearson.p)
                )
                CorrelationStatBox(
                    label: "Spearman rho",
                    value: String(format: "%.3f", r.spearman.rho),
                    significance: significanceStars(r.spearman.p)
                )
            }

            // Interpretation
            if let interp = r.interpretation, !interp.isEmpty {
                Text(interp)
                    .font(.callout)
                    .padding()
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.blue.opacity(0.08))
                    .clipShape(RoundedRectangle(cornerRadius: 10))
            }

            // Lag analysis
            if let lag = r.lagAnalysis {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Lag Analysis")
                        .font(.subheadline)
                        .fontWeight(.semibold)
                    HStack(spacing: 16) {
                        VStack(spacing: 2) {
                            Text("\(lag.optimalLagDays)d")
                                .font(.title3)
                                .fontWeight(.bold)
                            Text("Optimal Lag")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        VStack(spacing: 2) {
                            Text(String(format: "%.3f", lag.maxCorrelation))
                                .font(.title3)
                                .fontWeight(.bold)
                            Text("Peak r")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        if let dir = lag.direction {
                            VStack(spacing: 2) {
                                Text(dir.capitalized)
                                    .font(.title3)
                                    .fontWeight(.bold)
                                Text("Direction")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                .padding()
                .background(.background)
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .shadow(color: .black.opacity(0.05), radius: 2, y: 1)
            }
        }
    }

    private func significanceStars(_ p: Double) -> String {
        if p < 0.001 { return "***" }
        if p < 0.01 { return "**" }
        if p < 0.05 { return "*" }
        return "ns"
    }
}

private struct CorrelationStatBox: View {
    let label: String
    let value: String
    let significance: String

    var body: some View {
        VStack(spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text(value)
                    .font(.title3)
                    .fontWeight(.bold)
                    .monospacedDigit()
                Text(significance)
                    .font(.caption)
                    .foregroundStyle(significance == "ns" ? Color.secondary : Color.green)
            }
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 12)
        .background(.quaternary.opacity(0.5))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}
