import Foundation
import os

@MainActor @Observable
final class SyncManager {
    static let shared = SyncManager()

    var isSyncing = false
    var lastSyncDate: Date?
    var lastSyncResult: String?
    var syncLogs: [SyncLogEntry] = []

    // Live progress
    var syncPhase: String = ""
    var syncDetail: String = ""
    var recordsCollected: Int = 0
    var typesProcessed: Int = 0
    var totalTypes: Int = 0
    var batchesSent: Int = 0
    var totalBatches: Int = 0
    var recordsImportedSoFar: Int = 0
    var recordsSkippedSoFar: Int = 0

    // Timing for time-remaining estimates
    var syncStartTime: Date?
    var uploadStartTime: Date?

    var estimatedSecondsRemaining: Double? {
        guard isSyncing else { return nil }

        if syncPhase.starts(with: "Fetching"), totalTypes > 0, typesProcessed > 0,
           let start = syncStartTime {
            let elapsed = Date().timeIntervalSince(start)
            let fraction = Double(typesProcessed) / Double(totalTypes)
            return elapsed * (1.0 - fraction) / fraction
        }

        if let start = uploadStartTime, totalBatches > 0, batchesSent > 0 {
            let elapsed = Date().timeIntervalSince(start)
            let fraction = Double(batchesSent) / Double(totalBatches)
            return elapsed * (1.0 - fraction) / fraction
        }

        return nil
    }

    private let logger = Logger(subsystem: AppConstants.bundleIdentifier, category: "Sync")

    init() {
        loadLastSyncDate()
        loadSyncLogs()
    }

    private func resetProgress() {
        syncPhase = ""
        syncDetail = ""
        recordsCollected = 0
        typesProcessed = 0
        totalTypes = 0
        batchesSent = 0
        totalBatches = 0
        recordsImportedSoFar = 0
        recordsSkippedSoFar = 0
        syncStartTime = nil
        uploadStartTime = nil
    }

    // MARK: - Full Sync

    func performSync(trigger: SyncLogEntry.SyncTrigger) async {
        guard !isSyncing else {
            logger.info("Sync already in progress, skipping")
            return
        }

        isSyncing = true
        resetProgress()
        let startTime = Date()
        syncStartTime = startTime
        var totalSent = 0
        var totalImported = 0
        var totalSkipped = 0
        var syncError: String?

        let allConfigs = HealthKitTypeRegistry.allConfigs
        syncPhase = "Fetching health data"
        totalTypes = allConfigs.count

        do {
            // Fetch all HealthKit types concurrently
            let allRecords = try await fetchAllTypesConcurrently(configs: allConfigs)

            typesProcessed = allConfigs.count

            if allRecords.isEmpty {
                logger.info("No new records to sync")
                lastSyncResult = "No new data"
                lastSyncDate = Date()
                isSyncing = false
                resetProgress()
                saveLastSyncDate()
                addSyncLog(SyncLogEntry(
                    id: UUID(), timestamp: Date(), trigger: trigger,
                    recordsSent: 0, recordsImported: 0, skippedDuplicate: 0,
                    duration: Date().timeIntervalSince(startTime),
                    success: true, errorMessage: nil
                ))
                return
            }

            let batches = allRecords.chunked(into: AppConstants.batchSize)
            logger.info("Sending \(allRecords.count) records in \(batches.count) batches")

            syncPhase = "Uploading records"
            syncDetail = "\(allRecords.count) records"
            totalBatches = batches.count
            batchesSent = 0
            uploadStartTime = Date()

            for (index, batch) in batches.enumerated() {
                syncDetail = "Batch \(index + 1) of \(batches.count)"

                let response = try await sendBatchWithRetry(batch, batchIndex: index)
                totalSent += batch.count
                totalImported += response.imported ?? 0
                totalSkipped += response.skippedDuplicate ?? 0

                batchesSent = index + 1
                recordsImportedSoFar = totalImported
                recordsSkippedSoFar = totalSkipped
            }

            let duration = Date().timeIntervalSince(startTime)
            logger.info(
                "Sync complete: \(totalImported) imported, \(totalSkipped) skipped in \(String(format: "%.1f", duration))s"
            )

            lastSyncResult = "\(totalImported) imported, \(totalSkipped) duplicates"
            lastSyncDate = Date()
            isSyncing = false
            resetProgress()
            saveLastSyncDate()

        } catch {
            syncError = error.localizedDescription
            logger.error("Sync failed: \(error)")
            lastSyncResult = "Error: \(error.localizedDescription)"
            isSyncing = false
            resetProgress()
        }

        addSyncLog(SyncLogEntry(
            id: UUID(), timestamp: Date(), trigger: trigger,
            recordsSent: totalSent, recordsImported: totalImported,
            skippedDuplicate: totalSkipped,
            duration: Date().timeIntervalSince(startTime),
            success: syncError == nil, errorMessage: syncError
        ))
    }

    // MARK: - Concurrent HealthKit Fetch

    private struct FetchResult: Sendable {
        let records: [HealthRecord]
    }

    private func fetchAllTypesConcurrently(configs: [HealthKitTypeConfig]) async throws -> [HealthRecord] {
        let hkManager = HealthKitManager.shared

        // Use nonisolated local variables for the task group
        let results: [FetchResult] = try await withThrowingTaskGroup(of: FetchResult.self) { group in
            for config in configs {
                group.addTask {
                    if config.aggregation != nil {
                        let lastDate = await hkManager.savedLastSyncDate(for: config.metricKey)
                        let startDate = lastDate ?? Calendar.current.date(
                            byAdding: .day,
                            value: -AppConstants.defaultHistoryDays,
                            to: Date()
                        )!
                        let records = try await hkManager.fetchAggregatedSamples(
                            for: config, since: startDate
                        )
                        await hkManager.saveLastSyncDate(Date(), for: config.metricKey)
                        return FetchResult(records: records)
                    } else {
                        let anchor = await hkManager.savedAnchor(for: config.metricKey)
                        let (records, newAnchor) = try await hkManager.fetchNewSamples(
                            for: config, anchor: anchor
                        )
                        if let newAnchor {
                            await hkManager.saveAnchor(newAnchor, for: config.metricKey)
                        }
                        return FetchResult(records: records)
                    }
                }
            }

            var collected: [FetchResult] = []
            var completed = 0
            for try await result in group {
                collected.append(result)
                completed += 1
                // Update progress on main actor
                await MainActor.run {
                    self.typesProcessed = completed
                    self.recordsCollected += result.records.count
                    if completed % 5 == 0 || completed == configs.count {
                        self.syncDetail = "\(completed)/\(configs.count) types"
                    }
                }
            }
            return collected
        }

        let allRecords = results.flatMap { $0.records }
        if !allRecords.isEmpty {
            logger.info("Fetched \(allRecords.count) total records from \(configs.count) types")
        }
        return allRecords
    }

    // MARK: - Batch Send with Retry

    private nonisolated func sendBatchWithRetry(
        _ batch: [HealthRecord],
        batchIndex: Int
    ) async throws -> AppleHealthImportResponse {
        let logger = Logger(subsystem: AppConstants.bundleIdentifier, category: "Sync")
        var lastError: Error?

        for attempt in 0..<AppConstants.maxRetries {
            do {
                return try await HealthSyncAPIClient.shared.importRecords(batch)
            } catch SyncError.notAuthenticated {
                throw SyncError.notAuthenticated
            } catch {
                lastError = error
                let delay = AppConstants.retryBaseDelay * pow(2.0, Double(attempt))
                logger.warning(
                    "Batch \(batchIndex) attempt \(attempt + 1) failed, retrying in \(delay)s: \(error)"
                )
                try await Task.sleep(for: .seconds(delay))
            }
        }

        throw lastError ?? SyncError.noData
    }

    // MARK: - Persistence

    private func saveLastSyncDate() {
        UserDefaults.standard.set(Date(), forKey: AppConstants.lastSyncDateKey)
    }

    private func loadLastSyncDate() {
        lastSyncDate = UserDefaults.standard.object(forKey: AppConstants.lastSyncDateKey) as? Date
    }

    private func addSyncLog(_ entry: SyncLogEntry) {
        syncLogs.insert(entry, at: 0)
        if syncLogs.count > AppConstants.maxSyncLogEntries {
            syncLogs = Array(syncLogs.prefix(AppConstants.maxSyncLogEntries))
        }
        saveSyncLogs()
    }

    private func saveSyncLogs() {
        if let data = try? JSONEncoder().encode(syncLogs) {
            UserDefaults.standard.set(data, forKey: AppConstants.syncLogKey)
        }
    }

    private func loadSyncLogs() {
        if let data = UserDefaults.standard.data(forKey: AppConstants.syncLogKey),
            let logs = try? JSONDecoder().decode([SyncLogEntry].self, from: data)
        {
            syncLogs = logs
        }
    }
}

extension Array {
    func chunked(into size: Int) -> [[Element]] {
        stride(from: 0, to: count, by: size).map {
            Array(self[$0..<Swift.min($0 + size, count)])
        }
    }
}
