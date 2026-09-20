import BackgroundTasks
import os

enum BackgroundTaskManager {

    private static let logger = Logger(
        subsystem: AppConstants.bundleIdentifier,
        category: "BackgroundTask"
    )

    // MARK: - Registration

    static func registerBackgroundTasks() {
        BGTaskScheduler.shared.register(
            forTaskWithIdentifier: AppConstants.backgroundTaskIdentifier,
            using: nil
        ) { task in
            guard let refreshTask = task as? BGAppRefreshTask else { return }
            handleAppRefresh(task: refreshTask)
        }
        logger.info("Background task registered")
    }

    // MARK: - Scheduling

    static func scheduleAppRefresh() {
        let request = BGAppRefreshTaskRequest(
            identifier: AppConstants.backgroundTaskIdentifier
        )
        request.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)

        do {
            try BGTaskScheduler.shared.submit(request)
            logger.info("Background refresh scheduled for ~15 min")
        } catch {
            logger.error("Failed to schedule background refresh: \(error)")
        }
    }

    // MARK: - Handler

    private static func handleAppRefresh(task: BGAppRefreshTask) {
        scheduleAppRefresh()

        let syncTask = Task {
            await SyncManager.shared.performSync(trigger: .backgroundRefresh)
        }

        task.expirationHandler = {
            syncTask.cancel()
            logger.warning("Background task expired")
        }

        Task {
            _ = await syncTask.result
            task.setTaskCompleted(success: true)
        }
    }

    // MARK: - HealthKit Observer Setup

    static func setupHealthKitObservers() {
        Task {
            await HealthKitManager.shared.setupObserverQueries { metricKey in
                logger.info("Background delivery fired for \(metricKey)")

                let trigger: SyncLogEntry.SyncTrigger
                switch metricKey {
                case "workout":
                    trigger = .workoutDelivery
                case "dietaryEnergyConsumed":
                    trigger = .nutritionDelivery
                default:
                    trigger = .backgroundRefresh
                }

                Task {
                    await SyncManager.shared.performSync(trigger: trigger)
                }
            }
        }
    }
}
