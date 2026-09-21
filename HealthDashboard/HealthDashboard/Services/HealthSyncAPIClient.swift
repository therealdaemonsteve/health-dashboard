import Foundation
import os

actor HealthSyncAPIClient {
    static let shared = HealthSyncAPIClient()

    private let logger = Logger(subsystem: AppConstants.bundleIdentifier, category: "SyncAPI")

    private let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 120
        config.waitsForConnectivity = true
        return URLSession(configuration: config)
    }()

    private let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        return encoder
    }()

    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        return decoder
    }()

    func importRecords(_ records: [HealthRecord], finalBatch: Bool = false) async throws -> AppleHealthImportResponse {
        try await doImport(records, finalBatch: finalBatch, isRetry: false)
    }

    private func doImport(_ records: [HealthRecord], finalBatch: Bool, isRetry: Bool) async throws -> AppleHealthImportResponse {
        let token: String
        do {
            token = try await MCPClient.shared.getValidToken()
        } catch {
            throw SyncError.notAuthenticated
        }

        guard let url = URL(string: AppConstants.apiBaseURL + AppConstants.appleHealthImportPath)
        else {
            throw SyncError.invalidResponse
        }

        // Wrap records with final_batch flag
        struct ImportPayload: Encodable {
            let records: [HealthRecord]
            let final_batch: Bool
        }
        let payload = ImportPayload(records: records, final_batch: finalBatch)

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try encoder.encode(payload)

        let (data, response) = try await session.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw SyncError.invalidResponse
        }

        if httpResponse.statusCode == 401 {
            if !isRetry {
                // Try refreshing the token and retry once
                do {
                    try await MCPClient.shared.refreshAccessToken()
                    return try await doImport(records, finalBatch: finalBatch, isRetry: true)
                } catch {
                    throw SyncError.notAuthenticated
                }
            }
            throw SyncError.notAuthenticated
        }

        if httpResponse.statusCode >= 500 {
            throw SyncError.serverError(httpResponse.statusCode)
        }

        guard httpResponse.statusCode == 200 else {
            let body = String(data: data, encoding: .utf8) ?? "unknown"
            throw SyncError.httpError(httpResponse.statusCode, body)
        }

        return try decoder.decode(AppleHealthImportResponse.self, from: data)
    }
}

enum SyncError: Error, LocalizedError {
    case notAuthenticated
    case invalidResponse
    case serverError(Int)
    case httpError(Int, String)
    case noData

    var errorDescription: String? {
        switch self {
        case .notAuthenticated: return "Not authenticated. Please log in first."
        case .invalidResponse: return "Invalid server response."
        case .serverError(let code): return "Server error (\(code))."
        case .httpError(let code, let body): return "HTTP \(code): \(body)"
        case .noData: return "No data to sync."
        }
    }
}
