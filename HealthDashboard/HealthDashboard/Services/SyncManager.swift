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

    // MARK: - Full Sync Cache

    var hasResumableSync: Bool {
        FileManager.default.fileExists(atPath: fullSyncCacheURL.path)
    }

    private var fullSyncCacheURL: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("full_sync_cache.json")
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

    // MARK: - Delta Sync (Recent Data)

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
            let result = try await uploadBatches(
                batches, startingFrom: 0, persistProgress: false
            )
            totalSent = result.sent
            totalImported = result.imported
            totalSkipped = result.skipped

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

    // MARK: - Full Sync

    func performFullSync(trigger: SyncLogEntry.SyncTrigger) async {
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

        do {
            // 1. Delete any existing cache (fresh start)
            deleteFullSyncCache()

            // 2. Clear all anchors and sync dates
            clearAllAnchors()

            // 3. Fetch all types
            let allConfigs = HealthKitTypeRegistry.allConfigs
            syncPhase = "Fetching all health data"
            totalTypes = allConfigs.count

            let allRecords = try await fetchAllTypesConcurrently(configs: allConfigs)

            typesProcessed = allConfigs.count

            if allRecords.isEmpty {
                logger.info("Full sync: no records found")
                lastSyncResult = "No data found"
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

            // 4. Save cache before uploading
            let batches = allRecords.chunked(into: AppConstants.batchSize)
            let cache = FullSyncCache(
                records: allRecords, batchesSent: 0, totalBatches: batches.count
            )
            saveFullSyncCache(cache)
            logger.info("Full sync: cached \(allRecords.count) records in \(batches.count) batches")

            // 5. Upload batches, persisting progress
            let result = try await uploadBatches(
                batches, startingFrom: 0, persistProgress: true
            )
            totalSent = result.sent
            totalImported = result.imported
            totalSkipped = result.skipped

            // 6. Success — delete cache
            deleteFullSyncCache()

            let duration = Date().timeIntervalSince(startTime)
            logger.info(
                "Full sync complete: \(totalImported) imported, \(totalSkipped) skipped in \(String(format: "%.1f", duration))s"
            )

            lastSyncResult = "\(totalImported) imported, \(totalSkipped) duplicates"
            lastSyncDate = Date()
            isSyncing = false
            resetProgress()
            saveLastSyncDate()

        } catch {
            // 7. Failure — cache persists for resume
            syncError = error.localizedDescription
            logger.error("Full sync failed: \(error)")
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

    // MARK: - Resume Full Sync

    func resumeFullSync(trigger: SyncLogEntry.SyncTrigger) async {
        guard !isSyncing else {
            logger.info("Sync already in progress, skipping")
            return
        }

        guard let cache = loadFullSyncCache() else {
            logger.warning("Resume requested but no cache found")
            lastSyncResult = "No resumable sync found"
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

        do {
            let batches = cache.records.chunked(into: AppConstants.batchSize)
            let startBatch = cache.batchesSent
            logger.info(
                "Resuming full sync from batch \(startBatch + 1) of \(batches.count)"
            )

            syncPhase = "Resuming upload"

            let result = try await uploadBatches(
                batches, startingFrom: startBatch, persistProgress: true
            )
            totalSent = result.sent
            totalImported = result.imported
            totalSkipped = result.skipped

            // Success — delete cache
            deleteFullSyncCache()

            let duration = Date().timeIntervalSince(startTime)
            logger.info(
                "Resume sync complete: \(totalImported) imported, \(totalSkipped) skipped in \(String(format: "%.1f", duration))s"
            )

            lastSyncResult = "\(totalImported) imported, \(totalSkipped) duplicates"
            lastSyncDate = Date()
            isSyncing = false
            resetProgress()
            saveLastSyncDate()

        } catch {
            // Cache persists for another resume attempt
            syncError = error.localizedDescription
            logger.error("Resume sync failed: \(error)")
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

    // MARK: - Shared Upload

    private func uploadBatches(
        _ batches: [[HealthRecord]],
        startingFrom startBatch: Int,
        persistProgress: Bool
    ) async throws -> (sent: Int, imported: Int, skipped: Int) {
        let totalRecords = batches.flatMap { $0 }.count

        syncPhase = "Uploading records"
        syncDetail = "\(totalRecords) records"
        totalBatches = batches.count
        batchesSent = startBatch
        uploadStartTime = Date()

        var sent = 0
        var imported = 0
        var skipped = 0

        for index in startBatch..<batches.count {
            let batch = batches[index]
            let isLast = index == batches.count - 1
            syncDetail = "Batch \(index + 1) of \(batches.count)"

            let response = try await sendBatchWithRetry(batch, batchIndex: index, finalBatch: isLast)
            sent += batch.count
            imported += response.imported ?? 0
            skipped += response.skippedDuplicate ?? 0

            batchesSent = index + 1
            recordsImportedSoFar = imported
            recordsSkippedSoFar = skipped

            if persistProgress {
                updateCacheBatchesSent(index + 1)
            }
        }

        return (sent: sent, imported: imported, skipped: skipped)
    }

    // MARK: - Clear All Anchors

    func clearAllAnchors() {
        for config in HealthKitTypeRegistry.allConfigs {
            let anchorKey = AppConstants.syncAnchorPrefix + config.metricKey
            UserDefaults.standard.removeObject(forKey: anchorKey)
            let dateKey = AppConstants.aggregatedSyncDatePrefix + config.metricKey
            UserDefaults.standard.removeObject(forKey: dateKey)
        }
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
        batchIndex: Int,
        finalBatch: Bool = false
    ) async throws -> AppleHealthImportResponse {
        let logger = Logger(subsystem: AppConstants.bundleIdentifier, category: "Sync")
        var lastError: Error?

        for attempt in 0..<AppConstants.maxRetries {
            do {
                return try await HealthSyncAPIClient.shared.importRecords(batch, finalBatch: finalBatch)
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

    // MARK: - Cache Helpers

    private func saveFullSyncCache(_ cache: FullSyncCache) {
        do {
            let data = try JSONEncoder().encode(cache)
            try data.write(to: fullSyncCacheURL, options: .atomic)
        } catch {
            logger.error("Failed to save full sync cache: \(error)")
        }
    }

    private func loadFullSyncCache() -> FullSyncCache? {
        guard let data = try? Data(contentsOf: fullSyncCacheURL) else { return nil }
        return try? JSONDecoder().decode(FullSyncCache.self, from: data)
    }

    private func deleteFullSyncCache() {
        try? FileManager.default.removeItem(at: fullSyncCacheURL)
    }

    private func updateCacheBatchesSent(_ sent: Int) {
        guard var cache = loadFullSyncCache() else { return }
        cache.batchesSent = sent
        saveFullSyncCache(cache)
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
