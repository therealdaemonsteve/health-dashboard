import SwiftUI

@Observable
class BiomarkersViewModel {
    var biomarkers: [BiomarkerSummary] = []
    var isLoading = false
    var error: String?
    var searchText = ""

    var grouped: [(String, [BiomarkerSummary])] {
        let filtered = searchText.isEmpty
            ? biomarkers
            : biomarkers.filter { $0.name.localizedCaseInsensitiveContains(searchText) || $0.category.localizedCaseInsensitiveContains(searchText) }

        let dict = Dictionary(grouping: filtered, by: \.category)
        return dict.sorted { $0.key < $1.key }
    }

    func load() async {
        isLoading = true
        error = nil
        do {
            biomarkers = try await MCPClient.shared.listBiomarkers()
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }
}

struct BiomarkersTab: View {
    @State private var vm = BiomarkersViewModel()
    @State private var expandedCategories: Set<String> = []
    @State private var hasInitialized = false

    var body: some View {
        NavigationStack {
            Group {
                if vm.isLoading && vm.biomarkers.isEmpty {
                    ProgressView("Loading...")
                } else if let error = vm.error, vm.biomarkers.isEmpty {
                    ContentUnavailableView("Error", systemImage: "exclamationmark.triangle", description: Text(error))
                } else if vm.grouped.isEmpty {
                    ContentUnavailableView.search(text: vm.searchText)
                } else {
                    list
                }
            }
            .navigationTitle("Biomarkers")
            .searchable(text: $vm.searchText, prompt: "Search biomarkers")
            .refreshable { await vm.load() }
            .task {
                await vm.load()
                if !hasInitialized {
                    // Start with all categories expanded on first load
                    expandedCategories = Set(vm.grouped.map(\.0))
                    hasInitialized = true
                }
            }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        Button("Expand All", systemImage: "arrow.up.left.and.arrow.down.right") {
                            expandedCategories = Set(vm.grouped.map(\.0))
                        }
                        Button("Collapse All", systemImage: "arrow.down.right.and.arrow.up.left") {
                            expandedCategories.removeAll()
                        }
                    } label: {
                        Image(systemName: "line.3.horizontal.decrease")
                    }
                }
            }
        }
    }

    private var list: some View {
        List {
            ForEach(vm.grouped, id: \.0) { category, items in
                Section {
                    DisclosureGroup(
                        isExpanded: Binding(
                            get: { expandedCategories.contains(category) },
                            set: { isExpanded in
                                if isExpanded {
                                    expandedCategories.insert(category)
                                } else {
                                    expandedCategories.remove(category)
                                }
                            }
                        )
                    ) {
                        ForEach(items) { item in
                            NavigationLink(destination: BiomarkerDetailView(biomarkerName: item.name)) {
                                BiomarkerRow(item: item)
                            }
                        }
                    } label: {
                        HStack {
                            Text(category)
                                .font(.subheadline)
                                .fontWeight(.semibold)
                                .textCase(.uppercase)
                                .foregroundStyle(.secondary)
                            Spacer()
                            Text("\(items.count)")
                                .font(.caption)
                                .foregroundStyle(.tertiary)
                        }
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .onChange(of: vm.searchText) {
            if !vm.searchText.isEmpty {
                // Auto-expand all categories when searching
                expandedCategories = Set(vm.grouped.map(\.0))
            }
        }
    }
}

private struct BiomarkerRow: View {
    let item: BiomarkerSummary

    var body: some View {
        HStack {
            StatusDot(status: item.status)

            VStack(alignment: .leading, spacing: 2) {
                Text(item.name)
                    .fontWeight(.medium)
                if let date = item.latestDate {
                    Text(date)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Spacer()

            if let value = item.latestValue {
                VStack(alignment: .trailing, spacing: 2) {
                    Text(formatValue(value))
                        .fontWeight(.medium)
                        .monospacedDigit()
                    if let unit = item.unit {
                        Text(unit)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private func formatValue(_ v: Double) -> String {
        if v == v.rounded() && v < 10000 {
            return String(format: "%.0f", v)
        }
        return String(format: "%.1f", v)
    }
}
