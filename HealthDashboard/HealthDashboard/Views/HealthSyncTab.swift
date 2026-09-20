import SwiftUI

struct HealthSyncTab: View {
    @State private var syncManager = SyncManager.shared
    @State private var showingFullSyncAlert = false

    var body: some View {
        NavigationStack {
            List {
                if syncManager.isSyncing {
                    syncProgressSection
                } else {
                    syncStatusSection
                }

                Section("Actions") {
                    Button {
                        Task {
                            await syncManager.performSync(trigger: .manual)
                        }
                    } label: {
                        Label("Sync Recent Data", systemImage: "arrow.triangle.2.circlepath")
                    }
                    .disabled(syncManager.isSyncing)

                    Button {
                        showingFullSyncAlert = true
                    } label: {
                        Label("Full Sync", systemImage: "arrow.clockwise.circle")
                    }
                    .disabled(syncManager.isSyncing)

                    if syncManager.hasResumableSync {
                        Button {
                            Task {
                                await syncManager.resumeFullSync(trigger: .resumeFullSync)
                            }
                        } label: {
                            Label("Resume Full Sync", systemImage: "play.circle")
                        }
                        .disabled(syncManager.isSyncing)
                    }
                }

                Section("Recent Syncs") {
                    if syncManager.syncLogs.isEmpty {
                        Text("No sync history")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(syncManager.syncLogs) { log in
                            SyncLogRow(log: log)
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Health Sync")
            .refreshable {
                await syncManager.performSync(trigger: .manual)
            }
            .alert("Full Sync?", isPresented: $showingFullSyncAlert) {
                Button("Start Full Sync", role: .destructive) {
                    Task {
                        await syncManager.performFullSync(trigger: .fullSync)
                    }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text(
                    "This will clear all sync anchors and re-fetch your entire health history. The backend handles deduplication, so no data will be lost."
                )
            }
        }
    }

    // MARK: - Live Progress (shown while syncing)

    private var syncProgressSection: some View {
        Section {
            TimelineView(.periodic(from: .now, by: 1)) { _ in
                // Phase + detail
                HStack(spacing: 10) {
                    ProgressView()
                    VStack(alignment: .leading, spacing: 2) {
                        Text(syncManager.syncPhase)
                            .fontWeight(.medium)
                        Text(syncManager.syncDetail)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                // Progress bar
                if syncManager.syncPhase.starts(with: "Fetching") {
                    // Fetch phase: types processed / total
                    if syncManager.totalTypes > 0 {
                        VStack(alignment: .leading, spacing: 4) {
                            ProgressView(
                                value: Double(syncManager.typesProcessed),
                                total: Double(syncManager.totalTypes)
                            )
                            HStack {
                                Text("\(syncManager.typesProcessed)/\(syncManager.totalTypes) types")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                Spacer()
                                Text("\(syncManager.recordsCollected) records found")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            if let remaining = syncManager.estimatedSecondsRemaining {
                                Text(formatTimeRemaining(remaining))
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                } else if syncManager.totalBatches > 0 {
                    // Upload phase: batches sent / total
                    VStack(alignment: .leading, spacing: 4) {
                        ProgressView(
                            value: Double(syncManager.batchesSent),
                            total: Double(syncManager.totalBatches)
                        )
                        HStack {
                            Text("\(syncManager.batchesSent)/\(syncManager.totalBatches) batches")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                            Spacer()
                            if syncManager.recordsImportedSoFar > 0 || syncManager.recordsSkippedSoFar > 0 {
                                Text("\(syncManager.recordsImportedSoFar) imported, \(syncManager.recordsSkippedSoFar) skipped")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        if let remaining = syncManager.estimatedSecondsRemaining {
                            Text(formatTimeRemaining(remaining))
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        } header: {
            Text("Sync in Progress")
        }
    }

    private func formatTimeRemaining(_ seconds: Double) -> String {
        if seconds < 5 { return "Almost done" }
        if seconds < 60 { return "~\(Int(seconds))s remaining" }
        let mins = Int(seconds) / 60
        let secs = Int(seconds) % 60
        if secs == 0 { return "~\(mins)m remaining" }
        return "~\(mins)m \(secs)s remaining"
    }

    // MARK: - Idle Status (shown when not syncing)

    private var syncStatusSection: some View {
        Section("Sync Status") {
            HStack {
                Text("Status")
                Spacer()
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                Text("Idle")
                    .foregroundStyle(.secondary)
            }

            HStack {
                Text("Last Sync")
                Spacer()
                if let date = syncManager.lastSyncDate {
                    Text(date, style: .relative)
                        .foregroundStyle(.secondary)
                } else {
                    Text("Never")
                        .foregroundStyle(.secondary)
                }
            }

            if let result = syncManager.lastSyncResult {
                HStack {
                    Text("Result")
                    Spacer()
                    Text(result)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                        .multilineTextAlignment(.trailing)
                }
            }
        }
    }

}

// MARK: - Sync Log Row

private struct SyncLogRow: View {
    let log: SyncLogEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Image(systemName: log.success ? "checkmark.circle.fill" : "xmark.circle.fill")
                    .foregroundStyle(log.success ? .green : .red)
                    .font(.caption)
                Text(triggerLabel)
                    .font(.caption)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(.quaternary)
                    .clipShape(Capsule())
                Spacer()
                Text(log.timestamp, style: .relative)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            if log.recordsSent > 0 {
                Text(
                    "\(log.recordsImported) imported, \(log.skippedDuplicate) skipped (\(String(format: "%.1f", log.duration))s)"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            } else if log.success {
                Text("No new data")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if let error = log.errorMessage {
                Text(error)
                    .font(.caption2)
                    .foregroundStyle(.red)
                    .lineLimit(2)
            }
        }
        .padding(.vertical, 2)
    }

    private var triggerLabel: String {
        switch log.trigger {
        case .manual: return "Manual"
        case .backgroundRefresh: return "Background"
        case .workoutDelivery: return "Workout"
        case .nutritionDelivery: return "Nutrition"
        case .appLaunch: return "Launch"
        case .fullSync: return "Full"
        case .resumeFullSync: return "Resume"
        }
    }
}
