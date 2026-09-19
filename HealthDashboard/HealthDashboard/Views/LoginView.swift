import SwiftUI
import AuthenticationServices

struct LoginView: View {
    @Binding var isAuthenticated: Bool
    @State private var isLoading = false
    @State private var errorMessage: String?

    var body: some View {
        VStack(spacing: 32) {
            Spacer()

            VStack(spacing: 12) {
                Image(systemName: "heart.text.clipboard")
                    .font(.system(size: 64))
                    .foregroundStyle(.blue)

                Text("Health Dashboard")
                    .font(.largeTitle)
                    .fontWeight(.bold)

                Text("Sign in to access your health data")
                    .foregroundStyle(.secondary)
            }

            Spacer()

            if let error = errorMessage {
                Text(error)
                    .font(.callout)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
            }

            Button {
                Task { await signIn() }
            } label: {
                HStack {
                    if isLoading {
                        ProgressView()
                            .tint(.white)
                    } else {
                        Image(systemName: "key.fill")
                    }
                    Text("Sign in with PIN")
                }
                .frame(maxWidth: .infinity)
                .padding()
                .background(.blue)
                .foregroundStyle(.white)
                .fontWeight(.semibold)
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }
            .disabled(isLoading)
            .padding(.horizontal, 32)
            .padding(.bottom, 48)
        }
    }

    private func signIn() async {
        isLoading = true
        errorMessage = nil

        do {
            let presenter = WebAuthPresenter()
            _ = try await MCPClient.shared.startOAuthFlow(presenter: presenter)
            await MainActor.run {
                isAuthenticated = true
            }
        } catch {
            await MainActor.run {
                if (error as? ASWebAuthenticationSessionError)?.code == .canceledLogin {
                    errorMessage = nil
                } else {
                    errorMessage = error.localizedDescription
                }
            }
        }

        await MainActor.run {
            isLoading = false
        }
    }
}

@MainActor
class WebAuthPresenter: NSObject, ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        guard let scene = UIApplication.shared.connectedScenes.first as? UIWindowScene,
              let window = scene.windows.first else {
            return ASPresentationAnchor()
        }
        return window
    }
}
