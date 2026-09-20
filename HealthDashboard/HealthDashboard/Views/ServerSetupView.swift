import SwiftUI

struct ServerSetupView: View {
    @Binding var isConfigured: Bool
    @State private var serverURL = ""
    @State private var errorMessage: String?
    @State private var isTesting = false

    var body: some View {
        VStack(spacing: 32) {
            Spacer()

            VStack(spacing: 12) {
                Image(systemName: "server.rack")
                    .font(.system(size: 56))
                    .foregroundStyle(.blue)

                Text("Connect Your Server")
                    .font(.largeTitle)
                    .fontWeight(.bold)

                Text("Enter the URL of your Health Dashboard server. You'll find this in your deploy output after running deploy-mcp.sh.")
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("Server URL")
                    .font(.subheadline)
                    .fontWeight(.medium)
                TextField("https://your-lambda-url.on.aws", text: $serverURL)
                    .textContentType(.URL)
                    .keyboardType(.URL)
                    .autocapitalization(.none)
                    .autocorrectionDisabled()
                    .padding()
                    .background(.quaternary)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
            }
            .padding(.horizontal, 32)

            if let error = errorMessage {
                Text(error)
                    .font(.callout)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }

            Spacer()

            Button {
                Task { await connect() }
            } label: {
                HStack {
                    if isTesting {
                        ProgressView()
                            .tint(.white)
                    } else {
                        Image(systemName: "link")
                    }
                    Text("Connect")
                }
                .frame(maxWidth: .infinity)
                .padding()
                .background(isValid ? .blue : .gray)
                .foregroundStyle(.white)
                .fontWeight(.semibold)
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .disabled(!isValid || isTesting)
            .padding(.horizontal, 32)
            .padding(.bottom, 48)
        }
        .onAppear {
            let existing = AppConstants.apiBaseURL
            if !existing.isEmpty { serverURL = existing }
        }
    }

    private var isValid: Bool {
        !serverURL.isEmpty && serverURL.hasPrefix("https://")
    }

    private func connect() async {
        errorMessage = nil
        isTesting = true

        let url = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))

        // Test the server by hitting the OAuth metadata endpoint
        guard let testURL = URL(string: "\(url)/.well-known/oauth-authorization-server") else {
            errorMessage = "Invalid URL format"
            isTesting = false
            return
        }

        do {
            let (_, response) = try await URLSession.shared.data(from: testURL)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                errorMessage = "Server did not respond correctly. Check the URL and try again."
                isTesting = false
                return
            }
        } catch {
            errorMessage = "Could not reach server: \(error.localizedDescription)"
            isTesting = false
            return
        }

        // Save configuration
        AppConstants.configure(apiBaseURL: url)
        await MainActor.run {
            isConfigured = true
        }
        isTesting = false
    }
}
