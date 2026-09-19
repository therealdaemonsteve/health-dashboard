import SwiftUI

@main
struct HealthDashboardApp: App {
    @State private var isAuthenticated = false

    init() {
        BackgroundTaskManager.registerBackgroundTasks()
    }

    var body: some Scene {
        WindowGroup {
            Group {
                if isAuthenticated {
                    DashboardView(isAuthenticated: $isAuthenticated)
                } else {
                    LoginView(isAuthenticated: $isAuthenticated)
                }
            }
            .task {
                isAuthenticated = await MCPClient.shared.isAuthenticated
                if isAuthenticated {
                    await setupHealthSync()
                }
            }
            .onChange(of: isAuthenticated) { oldValue, newValue in
                if !oldValue && newValue {
                    // Just logged in — start health sync
                    Task { await setupHealthSync() }
                }
            }
        }
    }

    private func setupHealthSync() async {
        guard HealthKitManager.shared.isHealthDataAvailable else { return }

        do {
            try await HealthKitManager.shared.requestAuthorisation()
        } catch {
            // User may deny permissions; non-fatal
        }

        await HealthKitManager.shared.enableBackgroundDelivery()
        BackgroundTaskManager.setupHealthKitObservers()
        BackgroundTaskManager.scheduleAppRefresh()

        await SyncManager.shared.performSync(trigger: .appLaunch)

        // If sync failed due to auth, show login
        if await !MCPClient.shared.isAuthenticated {
            await MainActor.run { isAuthenticated = false }
        }
    }
}
