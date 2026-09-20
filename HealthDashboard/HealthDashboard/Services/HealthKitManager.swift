import HealthKit
import os

actor HealthKitManager {
    static let shared = HealthKitManager()

    private let healthStore = HKHealthStore()
    private let logger = Logger(subsystem: AppConstants.bundleIdentifier, category: "HealthKit")

    // MARK: - Availability & Authorisation

    nonisolated var isHealthDataAvailable: Bool {
        HKHealthStore.isHealthDataAvailable()
    }

    func requestAuthorisation() async throws {
        try await healthStore.requestAuthorization(toShare: [], read: HealthKitTypeRegistry.allReadTypes)
    }

    // MARK: - Background Delivery

    func enableBackgroundDelivery() async {
        for config in HealthKitTypeRegistry.backgroundDeliveryConfigs {
            do {
                try await healthStore.enableBackgroundDelivery(
                    for: config.sampleType,
                    frequency: config.backgroundDeliveryFrequency
                )
                logger.info("Enabled background delivery for \(config.metricKey)")
            } catch {
                logger.error("Failed background delivery for \(config.metricKey): \(error)")
            }
        }
    }

    // MARK: - Observer Queries

    func setupObserverQueries(onUpdate: @escaping @Sendable (String) -> Void) {
        for config in HealthKitTypeRegistry.backgroundDeliveryConfigs {
            let query = HKObserverQuery(sampleType: config.sampleType, predicate: nil) {
                _, completionHandler, error in
                if error == nil {
                    onUpdate(config.metricKey)
                }
                completionHandler()
            }
            healthStore.execute(query)
            logger.info("Observer query registered for \(config.metricKey)")
        }
    }

    // MARK: - Aggregated Queries (De-duplicated Daily Totals/Averages)

    /// Fetches daily aggregated values using HKStatisticsCollectionQuery.
    /// This automatically de-duplicates overlapping sources (iPhone + Apple Watch).
    func fetchAggregatedSamples(
        for config: HealthKitTypeConfig,
        since startDate: Date
    ) async throws -> [HealthRecord] {
        guard let quantityType = config.sampleType as? HKQuantityType,
              let aggregation = config.aggregation
        else {
            return []
        }

        let options: HKStatisticsOptions = switch aggregation {
        case .cumulativeSum: .cumulativeSum
        case .discreteAverage: .discreteAverage
        }

        let calendar = Calendar.current
        let anchorDate = calendar.startOfDay(for: Date())
        let interval = DateComponents(day: 1)
        let endDate = Date()

        // Re-fetch last 2 days to catch late-arriving data
        let adjustedStart = calendar.date(byAdding: .day, value: -2, to: startDate) ?? startDate

        let predicate = HKQuery.predicateForSamples(
            withStart: adjustedStart,
            end: endDate,
            options: .strictStartDate
        )

        return try await withCheckedThrowingContinuation { continuation in
            let query = HKStatisticsCollectionQuery(
                quantityType: quantityType,
                quantitySamplePredicate: predicate,
                options: options,
                anchorDate: anchorDate,
                intervalComponents: interval
            )

            query.initialResultsHandler = { _, collection, error in
                if let error {
                    continuation.resume(throwing: error)
                    return
                }

                guard let collection else {
                    continuation.resume(returning: [])
                    return
                }

                let formatter = ISO8601DateFormatter()
                formatter.formatOptions = [.withInternetDateTime]
                var records: [HealthRecord] = []

                collection.enumerateStatistics(from: adjustedStart, to: endDate) { statistics, _ in
                    let quantity: HKQuantity? = switch aggregation {
                    case .cumulativeSum: statistics.sumQuantity()
                    case .discreteAverage: statistics.averageQuantity()
                    }

                    guard let quantity else { return }
                    var value = quantity.doubleValue(for: config.unit)
                    // HKUnit.percent() returns 0-1 range; convert to 0-100 for display
                    if config.unit == .percent() { value *= 100 }
                    guard value > 0 else { return }

                    records.append(HealthRecord(
                        metric: config.metricKey,
                        value: value,
                        date: formatter.string(from: statistics.startDate),
                        unit: config.unitString
                    ))
                }

                continuation.resume(returning: records)
            }

            healthStore.execute(query)
        }
    }

    // MARK: - Anchored Queries (Individual Samples)

    func fetchNewSamples(
        for config: HealthKitTypeConfig,
        anchor: HKQueryAnchor?
    ) async throws -> ([HealthRecord], HKQueryAnchor?) {
        if config.sampleType is HKQuantityType {
            return try await fetchQuantitySamples(config: config, anchor: anchor)
        } else if config.metricKey == "workout" {
            return try await fetchWorkoutSamples(anchor: anchor)
        } else if config.sampleType is HKCategoryType {
            return try await fetchCategorySamples(config: config, anchor: anchor)
        }
        return ([], anchor)
    }

    // MARK: - Quantity Samples

    private func fetchQuantitySamples(
        config: HealthKitTypeConfig,
        anchor: HKQueryAnchor?
    ) async throws -> ([HealthRecord], HKQueryAnchor?) {
        guard let quantityType = config.sampleType as? HKQuantityType else {
            return ([], anchor)
        }

        return try await withCheckedThrowingContinuation { continuation in
            let query = HKAnchoredObjectQuery(
                type: quantityType,
                predicate: nil,
                anchor: anchor,
                limit: HKObjectQueryNoLimit
            ) { _, samplesOrNil, _, newAnchor, error in
                if let error {
                    continuation.resume(throwing: error)
                    return
                }

                let samples = (samplesOrNil as? [HKQuantitySample]) ?? []
                let formatter = ISO8601DateFormatter()
                formatter.formatOptions = [.withInternetDateTime]

                let records = samples.map { sample in
                    var value = sample.quantity.doubleValue(for: config.unit)
                    // HKUnit.percent() returns 0-1 range; convert to 0-100 for display
                    if config.unit == .percent() { value *= 100 }
                    return HealthRecord(
                        metric: config.metricKey,
                        value: value,
                        date: formatter.string(from: sample.startDate),
                        unit: config.unitString
                    )
                }

                continuation.resume(returning: (records, newAnchor))
            }

            healthStore.execute(query)
        }
    }

    // MARK: - Workout Samples

    private func fetchWorkoutSamples(
        anchor: HKQueryAnchor?
    ) async throws -> ([HealthRecord], HKQueryAnchor?) {
        return try await withCheckedThrowingContinuation { continuation in
            let query = HKAnchoredObjectQuery(
                type: HKWorkoutType.workoutType(),
                predicate: nil,
                anchor: anchor,
                limit: HKObjectQueryNoLimit
            ) { _, samplesOrNil, _, newAnchor, error in
                if let error {
                    continuation.resume(throwing: error)
                    return
                }

                let workouts = (samplesOrNil as? [HKWorkout]) ?? []
                let formatter = ISO8601DateFormatter()
                formatter.formatOptions = [.withInternetDateTime]

                var records: [HealthRecord] = []

                for workout in workouts {
                    let dateString = formatter.string(from: workout.startDate)
                    let durationMinutes = workout.duration / 60.0

                    records.append(HealthRecord(
                        metric: "workout",
                        value: durationMinutes,
                        date: dateString,
                        unit: "min"
                    ))

                    if let energy = workout.totalEnergyBurned {
                        records.append(HealthRecord(
                            metric: "workoutEnergyBurned",
                            value: energy.doubleValue(for: .kilocalorie()),
                            date: dateString,
                            unit: "kcal"
                        ))
                    }

                    if let distance = workout.totalDistance {
                        records.append(HealthRecord(
                            metric: "workoutDistance",
                            value: distance.doubleValue(for: .meterUnit(with: .kilo)),
                            date: dateString,
                            unit: "km"
                        ))
                    }
                }

                continuation.resume(returning: (records, newAnchor))
            }

            healthStore.execute(query)
        }
    }

    // MARK: - Category Samples

    private func fetchCategorySamples(
        config: HealthKitTypeConfig,
        anchor: HKQueryAnchor?
    ) async throws -> ([HealthRecord], HKQueryAnchor?) {
        guard let categoryType = config.sampleType as? HKCategoryType else {
            return ([], anchor)
        }

        return try await withCheckedThrowingContinuation { continuation in
            let query = HKAnchoredObjectQuery(
                type: categoryType,
                predicate: nil,
                anchor: anchor,
                limit: HKObjectQueryNoLimit
            ) { _, samplesOrNil, _, newAnchor, error in
                if let error {
                    continuation.resume(throwing: error)
                    return
                }

                let samples = (samplesOrNil as? [HKCategorySample]) ?? []
                let formatter = ISO8601DateFormatter()
                formatter.formatOptions = [.withInternetDateTime]

                var records: [HealthRecord] = []

                for sample in samples {
                    if config.metricKey == "sleepAnalysis" {
                        // Sleep: filter to actual sleep stages, record duration
                        let sleepValue = HKCategoryValueSleepAnalysis(rawValue: sample.value)
                        let isActualSleep: Bool
                        if #available(iOS 16.0, *) {
                            isActualSleep = (sleepValue == .asleepCore
                                || sleepValue == .asleepDeep
                                || sleepValue == .asleepREM
                                || sleepValue == .asleepUnspecified)
                        } else {
                            isActualSleep = (sleepValue == .asleep)
                        }
                        guard isActualSleep else { continue }
                        let durationMinutes = sample.endDate.timeIntervalSince(sample.startDate) / 60.0
                        records.append(HealthRecord(
                            metric: config.metricKey,
                            value: durationMinutes,
                            date: formatter.string(from: sample.startDate),
                            unit: config.unitString
                        ))
                    } else if config.metricKey == "mindfulSession" {
                        // Mindful session: record duration in minutes
                        let durationMinutes = sample.endDate.timeIntervalSince(sample.startDate) / 60.0
                        records.append(HealthRecord(
                            metric: config.metricKey,
                            value: durationMinutes,
                            date: formatter.string(from: sample.startDate),
                            unit: config.unitString
                        ))
                    } else if config.metricKey == "appleStandHour" {
                        // Stand hour: count "stood" hours (value == 0 means stood)
                        if sample.value == HKCategoryValueAppleStandHour.stood.rawValue {
                            records.append(HealthRecord(
                                metric: config.metricKey,
                                value: 1,
                                date: formatter.string(from: sample.startDate),
                                unit: config.unitString
                            ))
                        }
                    } else {
                        // Heart rate events and other category types: record as count of 1 per event
                        records.append(HealthRecord(
                            metric: config.metricKey,
                            value: 1,
                            date: formatter.string(from: sample.startDate),
                            unit: config.unitString
                        ))
                    }
                }

                continuation.resume(returning: (records, newAnchor))
            }

            healthStore.execute(query)
        }
    }

    // MARK: - Anchor Persistence

    func savedAnchor(for metricKey: String) -> HKQueryAnchor? {
        let key = AppConstants.syncAnchorPrefix + metricKey
        guard let data = UserDefaults.standard.data(forKey: key) else { return nil }
        return try? NSKeyedUnarchiver.unarchivedObject(ofClass: HKQueryAnchor.self, from: data)
    }

    func saveAnchor(_ anchor: HKQueryAnchor?, for metricKey: String) {
        let key = AppConstants.syncAnchorPrefix + metricKey
        if let anchor,
            let data = try? NSKeyedArchiver.archivedData(
                withRootObject: anchor, requiringSecureCoding: true)
        {
            UserDefaults.standard.set(data, forKey: key)
        } else {
            UserDefaults.standard.removeObject(forKey: key)
        }
    }

    // MARK: - Aggregated Sync Date Persistence

    func savedLastSyncDate(for metricKey: String) -> Date? {
        let key = AppConstants.aggregatedSyncDatePrefix + metricKey
        return UserDefaults.standard.object(forKey: key) as? Date
    }

    func saveLastSyncDate(_ date: Date, for metricKey: String) {
        let key = AppConstants.aggregatedSyncDatePrefix + metricKey
        UserDefaults.standard.set(date, forKey: key)
    }

    func clearLastSyncDate(for metricKey: String) {
        let key = AppConstants.aggregatedSyncDatePrefix + metricKey
        UserDefaults.standard.removeObject(forKey: key)
    }
}
