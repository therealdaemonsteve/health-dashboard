import Foundation
@preconcurrency import AuthenticationServices
import CryptoKit

actor MCPClient {
    static let shared = MCPClient()

    private let baseURL = "https://nwoyzbofonszvggd5wo2pqvida0fvcqq.lambda-url.eu-west-2.on.aws"
    private let redirectURI = "healthdashboard://oauth/callback"
    private let keychainTokenKey = "oauth_access_token"
    private let keychainRefreshTokenKey = "oauth_refresh_token"
    private let keychainClientIdKey = "oauth_client_id"
    private var requestId = 0
    private var isInitialized = false
    private var cachedAccessToken: String?
    private var isRefreshing = false

    private let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 60
        config.waitsForConnectivity = true
        return URLSession(configuration: config)
    }()

    private func loadAccessToken() -> String? {
        if let cached = cachedAccessToken { return cached }
        let stored = KeychainHelper.loadString(key: keychainTokenKey)
        if let stored { cachedAccessToken = stored }
        return stored
    }

    var isAuthenticated: Bool {
        cachedAccessToken != nil || KeychainHelper.loadString(key: keychainTokenKey) != nil
    }

    /// Returns a valid access token, attempting refresh if needed.
    func getValidToken() async throws -> String {
        if let token = loadAccessToken() { return token }
        // Try refresh
        try await refreshAccessToken()
        if let token = cachedAccessToken { return token }
        throw MCPError.notAuthenticated
    }

    // MARK: - OAuth Flow

    func startOAuthFlow(presenter: ASWebAuthenticationPresentationContextProviding) async throws -> String {
        // 1. Discover endpoints
        let metadata = try await discoverMetadata()
        guard let registrationEndpoint = metadata.registrationEndpoint,
              let authorizationEndpoint = metadata.authorizationEndpoint,
              let tokenEndpoint = metadata.tokenEndpoint else {
            throw MCPError.invalidMetadata
        }

        // 2. Register client
        let clientId = try await registerClient(endpoint: registrationEndpoint)

        // 3. Generate PKCE
        let codeVerifier = generateCodeVerifier()
        let codeChallenge = generateCodeChallenge(verifier: codeVerifier)

        // 4. Authorize via web (user enters PIN)
        let code = try await authorize(
            endpoint: authorizationEndpoint,
            clientId: clientId,
            codeChallenge: codeChallenge,
            presenter: presenter
        )

        // 5. Exchange code for token
        let tokenResponse = try await exchangeToken(
            endpoint: tokenEndpoint,
            code: code,
            codeVerifier: codeVerifier,
            clientId: clientId
        )

        // 6. Store tokens
        try KeychainHelper.saveString(key: keychainTokenKey, value: tokenResponse.accessToken)
        try KeychainHelper.saveString(key: keychainClientIdKey, value: clientId)
        if let refreshToken = tokenResponse.refreshToken {
            try KeychainHelper.saveString(key: keychainRefreshTokenKey, value: refreshToken)
        }
        cachedAccessToken = tokenResponse.accessToken

        return tokenResponse.accessToken
    }

    private func discoverMetadata() async throws -> OAuthServerMetadata {
        let url = URL(string: "\(baseURL)/.well-known/oauth-authorization-server")!
        let (data, _) = try await session.data(from: url)
        return try JSONDecoder().decode(OAuthServerMetadata.self, from: data)
    }

    private func registerClient(endpoint: String) async throws -> String {
        var request = URLRequest(url: URL(string: endpoint)!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body: [String: Any] = [
            "client_name": "HealthDashboard-iOS",
            "redirect_uris": [redirectURI]
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, _) = try await session.data(for: request)
        let registration = try JSONDecoder().decode(OAuthClientRegistration.self, from: data)
        return registration.clientId
    }

    private func authorize(
        endpoint: String,
        clientId: String,
        codeChallenge: String,
        presenter: ASWebAuthenticationPresentationContextProviding
    ) async throws -> String {
        var components = URLComponents(string: endpoint)!
        components.queryItems = [
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "client_id", value: clientId),
            URLQueryItem(name: "redirect_uri", value: redirectURI),
            URLQueryItem(name: "code_challenge", value: codeChallenge),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "state", value: UUID().uuidString),
            URLQueryItem(name: "scope", value: ""),
        ]

        let authURL = components.url!

        return try await withCheckedThrowingContinuation { continuation in
            let authSession = ASWebAuthenticationSession(
                url: authURL,
                callbackURLScheme: "healthdashboard"
            ) { callbackURL, error in
                if let error = error {
                    continuation.resume(throwing: error)
                    return
                }
                guard let callbackURL = callbackURL,
                      let components = URLComponents(url: callbackURL, resolvingAgainstBaseURL: false),
                      let code = components.queryItems?.first(where: { $0.name == "code" })?.value else {
                    continuation.resume(throwing: MCPError.noAuthCode)
                    return
                }
                continuation.resume(returning: code)
            }
            authSession.presentationContextProvider = presenter
            authSession.prefersEphemeralWebBrowserSession = false

            DispatchQueue.main.async {
                authSession.start()
            }
        }
    }

    private func exchangeToken(
        endpoint: String,
        code: String,
        codeVerifier: String,
        clientId: String
    ) async throws -> OAuthTokenResponse {
        var request = URLRequest(url: URL(string: endpoint)!)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")

        let params = [
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": codeVerifier,
            "client_id": clientId,
            "redirect_uri": redirectURI,
        ]
        request.httpBody = params
            .map { "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? $0.value)" }
            .joined(separator: "&")
            .data(using: .utf8)

        let (data, _) = try await session.data(for: request)
        return try JSONDecoder().decode(OAuthTokenResponse.self, from: data)
    }

    // MARK: - Token Refresh

    func refreshAccessToken() async throws {
        guard !isRefreshing else { return }
        isRefreshing = true
        defer { isRefreshing = false }

        guard let refreshToken = KeychainHelper.loadString(key: keychainRefreshTokenKey) else {
            throw MCPError.notAuthenticated
        }

        let metadata = try await discoverMetadata()
        guard let tokenEndpoint = metadata.tokenEndpoint else {
            throw MCPError.invalidMetadata
        }

        var request = URLRequest(url: URL(string: tokenEndpoint)!)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")

        let params = [
            "grant_type": "refresh_token",
            "refresh_token": refreshToken,
        ]
        request.httpBody = params
            .map { "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? $0.value)" }
            .joined(separator: "&")
            .data(using: .utf8)

        let (data, response) = try await session.data(for: request)

        if let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode != 200 {
            // Refresh token is invalid/expired — clear everything
            clearTokens()
            throw MCPError.notAuthenticated
        }

        let tokenResponse = try JSONDecoder().decode(OAuthTokenResponse.self, from: data)
        try KeychainHelper.saveString(key: keychainTokenKey, value: tokenResponse.accessToken)
        if let newRefresh = tokenResponse.refreshToken {
            try KeychainHelper.saveString(key: keychainRefreshTokenKey, value: newRefresh)
        }
        cachedAccessToken = tokenResponse.accessToken
    }

    // MARK: - PKCE Helpers

    private func generateCodeVerifier() -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        return Data(bytes)
            .base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    private func generateCodeChallenge(verifier: String) -> String {
        let hash = SHA256.hash(data: Data(verifier.utf8))
        return Data(hash)
            .base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    // MARK: - Logout

    func logout() {
        clearTokens()
    }

    private func clearTokens() {
        KeychainHelper.delete(key: keychainTokenKey)
        KeychainHelper.delete(key: keychainRefreshTokenKey)
        KeychainHelper.delete(key: keychainClientIdKey)
        cachedAccessToken = nil
        isInitialized = false
    }

    // MARK: - MCP JSON-RPC

    private func nextId() -> Int {
        requestId += 1
        return requestId
    }

    private func ensureInitialized() async throws {
        guard !isInitialized else { return }
        let body: [String: Any] = [
            "jsonrpc": "2.0",
            "id": nextId(),
            "method": "initialize",
            "params": [
                "protocolVersion": "2025-03-26",
                "capabilities": [:] as [String: Any],
                "clientInfo": [
                    "name": "HealthDashboard-iOS",
                    "version": "1.0.0"
                ]
            ]
        ]
        let _: [String: AnyCodable] = try await sendRPC(body: body)

        // Send initialized notification
        let notification: [String: Any] = [
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": [:] as [String: Any]
        ]
        _ = try? await sendRPCRaw(body: notification)
        isInitialized = true
    }

    func callTool<T: Decodable>(name: String, arguments: [String: Any]? = nil) async throws -> T {
        try await ensureInitialized()

        let body: [String: Any] = [
            "jsonrpc": "2.0",
            "id": nextId(),
            "method": "tools/call",
            "params": [
                "name": name,
                "arguments": arguments ?? [:]
            ] as [String: Any]
        ]

        let result: MCPToolResult = try await sendRPC(body: body)

        guard let content = result.content, let first = content.first, let text = first.text else {
            throw MCPError.emptyResponse
        }

        if result.isError == true {
            throw MCPError.toolError(text)
        }

        let textData = Data(text.utf8)
        do {
            return try JSONDecoder().decode(T.self, from: textData)
        } catch {
            throw MCPError.decodingError(type: String(describing: T.self), detail: error.localizedDescription)
        }
    }

    private func sendRPC<T: Decodable>(body: [String: Any], isRetry: Bool = false) async throws -> T {
        guard let token = loadAccessToken() else {
            throw MCPError.notAuthenticated
        }

        var request = URLRequest(url: URL(string: "\(baseURL)/mcp")!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json, */*", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response) = try await session.data(for: request)

        if let httpResponse = response as? HTTPURLResponse {
            if httpResponse.statusCode == 401 {
                if !isRetry {
                    // Try refreshing the token and retry once
                    do {
                        try await refreshAccessToken()
                        return try await sendRPC(body: body, isRetry: true)
                    } catch {
                        clearTokens()
                        throw MCPError.notAuthenticated
                    }
                }
                clearTokens()
                throw MCPError.notAuthenticated
            }
            if httpResponse.statusCode == 202 {
                throw MCPError.emptyResponse
            }
            if httpResponse.statusCode >= 500 {
                throw MCPError.serverError(httpResponse.statusCode)
            }
        }

        let rpcResponse = try JSONDecoder().decode(JSONRPCResponse<T>.self, from: data)

        if let error = rpcResponse.error {
            throw MCPError.rpcError(error.code, error.message)
        }

        guard let result = rpcResponse.result else {
            throw MCPError.emptyResponse
        }

        return result
    }

    private func sendRPCRaw(body: [String: Any]) async throws -> Data {
        guard let token = loadAccessToken() else {
            throw MCPError.notAuthenticated
        }

        var request = URLRequest(url: URL(string: "\(baseURL)/mcp")!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json, */*", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, _) = try await session.data(for: request)
        return data
    }

    // MARK: - Typed API Methods

    func getHealthOverview() async throws -> HealthOverview {
        try await callTool(name: "get_health_overview")
    }

    func listBiomarkers(category: String? = nil, status: String? = nil) async throws -> [BiomarkerSummary] {
        var args: [String: Any] = [:]
        if let category { args["category"] = category }
        if let status { args["status"] = status }
        return try await callTool(name: "list_biomarkers", arguments: args.isEmpty ? nil : args)
    }

    func getBiomarkerDetail(name: String) async throws -> BiomarkerDetail {
        try await callTool(name: "get_biomarker_detail", arguments: ["name": name])
    }

    func getMeasurements(biomarker: String, startDate: String? = nil, endDate: String? = nil) async throws -> MeasurementsResponse {
        var args: [String: Any] = ["biomarker": biomarker]
        if let startDate { args["start_date"] = startDate }
        if let endDate { args["end_date"] = endDate }
        return try await callTool(name: "get_measurements", arguments: args)
    }

    func getFlaggedBiomarkers() async throws -> [FlaggedBiomarker] {
        try await callTool(name: "get_flagged_biomarkers")
    }

    func getEvents(eventType: String? = nil, startDate: String? = nil, endDate: String? = nil) async throws -> [HealthEvent] {
        var args: [String: Any] = [:]
        if let eventType { args["event_type"] = eventType }
        if let startDate { args["start_date"] = startDate }
        if let endDate { args["end_date"] = endDate }
        return try await callTool(name: "get_events", arguments: args.isEmpty ? nil : args)
    }

    func getCoachingBrief() async throws -> CoachingBrief {
        try await callTool(name: "get_coaching_brief")
    }

    func updateGoal(goalId: String, status: String? = nil, title: String? = nil, progressNote: String? = nil) async throws -> UpdateGoalResponse {
        var args: [String: Any] = ["goal_id": goalId]
        if let status { args["status"] = status }
        if let title { args["title"] = title }
        if let progressNote { args["progress_note"] = progressNote }
        return try await callTool(name: "update_goal", arguments: args)
    }

    func addCoachingNote(text: String, tags: String? = nil, date: String? = nil) async throws -> AddCoachingNoteResponse {
        var args: [String: Any] = ["text": text]
        if let tags { args["tags"] = tags }
        if let date { args["date"] = date }
        return try await callTool(name: "add_coaching_note", arguments: args)
    }

    func addActionItem(title: String, dueDate: String? = nil, goalId: String? = nil) async throws -> AddActionItemResponse {
        var args: [String: Any] = ["title": title]
        if let dueDate { args["due_date"] = dueDate }
        if let goalId { args["goal_id"] = goalId }
        return try await callTool(name: "add_action_item", arguments: args)
    }

    func updateActionItem(actionId: String, status: String? = nil, title: String? = nil, dueDate: String? = nil) async throws -> UpdateActionItemResponse {
        var args: [String: Any] = ["action_id": actionId]
        if let status { args["status"] = status }
        if let title { args["title"] = title }
        if let dueDate { args["due_date"] = dueDate }
        return try await callTool(name: "update_action_item", arguments: args)
    }

    func addMeasurement(date: String, biomarker: String, value: Double, unit: String) async throws -> AddMeasurementResponse {
        try await callTool(name: "add_measurement", arguments: [
            "date": date,
            "biomarker": biomarker,
            "value": value,
            "unit": unit,
        ] as [String: Any])
    }

    func deleteMeasurement(measurementId: String) async throws -> DeleteMeasurementResponse {
        try await callTool(name: "delete_measurement", arguments: ["measurement_id": measurementId])
    }

    func getCategorySummary(category: String) async throws -> CategorySummary {
        try await callTool(name: "get_category_summary", arguments: ["category": category])
    }

    // MARK: - Analytics

    func getRollingAverages(biomarker: String, windowDays: Int = 7, startDate: String? = nil, endDate: String? = nil) async throws -> RollingAverageResponse {
        var args: [String: Any] = ["biomarker": biomarker, "window_days": windowDays]
        if let startDate { args["start_date"] = startDate }
        if let endDate { args["end_date"] = endDate }
        return try await callTool(name: "get_rolling_averages", arguments: args)
    }

    func detectTrends(lookbackDays: Int = 90, biomarker: String? = nil) async throws -> TrendDetectionResponse {
        var args: [String: Any] = ["lookback_days": lookbackDays]
        if let biomarker { args["biomarker"] = biomarker }
        return try await callTool(name: "detect_trends", arguments: args)
    }

    func computeCorrelation(biomarkerA: String, biomarkerB: String, startDate: String? = nil, endDate: String? = nil) async throws -> CorrelationResponse {
        var args: [String: Any] = ["biomarker_a": biomarkerA, "biomarker_b": biomarkerB]
        if let startDate { args["start_date"] = startDate }
        if let endDate { args["end_date"] = endDate }
        return try await callTool(name: "compute_correlation", arguments: args)
    }

    func analyseEventImpact(eventId: String, biomarkers: [String]? = nil, windowDays: Int = 60) async throws -> EventImpactResponse {
        var args: [String: Any] = ["event_id": eventId, "window_days": windowDays]
        if let biomarkers { args["biomarkers"] = biomarkers }
        return try await callTool(name: "analyse_event_impact", arguments: args)
    }

    func getHealthScores(category: String? = nil) async throws -> HealthScoresResponse {
        var args: [String: Any] = [:]
        if let category { args["category"] = category }
        return try await callTool(name: "get_health_scores", arguments: args.isEmpty ? nil : args)
    }

    func getGoalProgress(goalId: String? = nil) async throws -> GoalProgressResponse {
        var args: [String: Any] = [:]
        if let goalId { args["goal_id"] = goalId }
        return try await callTool(name: "get_goal_progress", arguments: args.isEmpty ? nil : args)
    }

    func generateInsights(biomarker: String? = nil) async throws -> GenerateInsightsResponse {
        var args: [String: Any] = [:]
        if let biomarker { args["biomarker"] = biomarker }
        return try await callTool(name: "generate_insights", arguments: args.isEmpty ? nil : args)
    }

    func addGoal(title: String, category: String? = nil, targetValue: Double? = nil, targetUnit: String? = nil, targetDate: String? = nil, linkedBiomarker: String? = nil) async throws -> AddGoalResponse {
        var args: [String: Any] = ["title": title]
        if let category { args["category"] = category }
        if let targetValue { args["target_value"] = targetValue }
        if let targetUnit { args["target_unit"] = targetUnit }
        if let targetDate { args["target_date"] = targetDate }
        if let linkedBiomarker { args["linked_biomarker"] = linkedBiomarker }
        return try await callTool(name: "add_goal", arguments: args)
    }

    // MARK: - Phases

    func getPhases(status: String? = nil) async throws -> PhasesListResponse {
        var args: [String: Any] = [:]
        if let status { args["status"] = status }
        return try await callTool(name: "get_phases", arguments: args.isEmpty ? nil : args)
    }

    func getPhaseDetail(phaseId: String) async throws -> PhaseItem {
        try await callTool(name: "get_phase_detail", arguments: ["phase_id": phaseId])
    }

    func addPhase(name: String, startDate: String, target: String? = nil, supplements: [Supplement]? = nil, medications: [Medication]? = nil, notes: String? = nil) async throws -> AddPhaseResponse {
        var args: [String: Any] = ["name": name, "start_date": startDate]
        if let target { args["target"] = target }
        if let notes { args["notes"] = notes }
        if let supplements, !supplements.isEmpty {
            let data = try JSONEncoder().encode(supplements)
            args["supplements"] = String(data: data, encoding: .utf8) ?? "[]"
        }
        if let medications, !medications.isEmpty {
            let data = try JSONEncoder().encode(medications)
            args["medications"] = String(data: data, encoding: .utf8) ?? "[]"
        }
        return try await callTool(name: "add_phase", arguments: args)
    }

    func updatePhase(phaseId: String, name: String? = nil, status: String? = nil, endDate: String? = nil, target: String? = nil, supplements: [Supplement]? = nil, medications: [Medication]? = nil, notes: String? = nil) async throws -> UpdatePhaseResponse {
        var args: [String: Any] = ["phase_id": phaseId]
        if let name { args["name"] = name }
        if let status { args["status"] = status }
        if let endDate { args["end_date"] = endDate }
        if let target { args["target"] = target }
        if let notes { args["notes"] = notes }
        if let supplements {
            let data = try JSONEncoder().encode(supplements)
            args["supplements"] = String(data: data, encoding: .utf8) ?? "[]"
        }
        if let medications {
            let data = try JSONEncoder().encode(medications)
            args["medications"] = String(data: data, encoding: .utf8) ?? "[]"
        }
        return try await callTool(name: "update_phase", arguments: args)
    }

    // MARK: - Checklist

    func getChecklist(date: String? = nil) async throws -> ChecklistResponse {
        var args: [String: Any] = [:]
        if let date { args["date"] = date }
        return try await callTool(name: "get_checklist", arguments: args.isEmpty ? nil : args)
    }

    func toggleChecklist(itemId: String, date: String? = nil) async throws -> ToggleChecklistResponse {
        var args: [String: Any] = ["item_id": itemId]
        if let date { args["date"] = date }
        return try await callTool(name: "toggle_checklist", arguments: args)
    }
}

// MARK: - Errors

enum MCPError: Error, LocalizedError {
    case notAuthenticated
    case invalidMetadata
    case noAuthCode
    case emptyResponse
    case toolError(String)
    case rpcError(Int, String)
    case serverError(Int)
    case decodingError(type: String, detail: String)

    var errorDescription: String? {
        switch self {
        case .notAuthenticated: return "Not authenticated. Please log in."
        case .invalidMetadata: return "Invalid OAuth server metadata."
        case .noAuthCode: return "No authorization code received."
        case .emptyResponse: return "Empty response from server."
        case .toolError(let msg): return msg
        case .rpcError(_, let msg): return msg
        case .serverError(let code): return "Server error (\(code)). Please try again."
        case .decodingError(let type, let detail): return "Failed to decode \(type): \(detail)"
        }
    }
}
